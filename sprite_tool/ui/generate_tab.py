import json
import os
import random
import re
import shutil
from html import escape
from typing import List, Optional

from PIL import Image
from PyQt5.QtCore import Qt, QTimer, QUrl
from PyQt5.QtGui import QColor, QDesktopServices
from PyQt5.QtWidgets import (
    QAction,
    QCheckBox,
    QComboBox,
    QColorDialog,
    QDialog,
    QDialogButtonBox,
    QDockWidget,
    QFrame,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTabWidget,
    QTextEdit,
    QToolBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from sprite_tool.constants import IMAGE_FILTER, JSON_FORMATS, KTX2_MODES, MIPMAP_FILTERS, PACKING_ALGORITHMS, PIXEL_FORMATS
from sprite_tool.models import GodotSettings, Ktx2Settings, OptimizationSettings
from sprite_tool.services.godot import export_godot_resources, res_path_for
from sprite_tool.services.images import compose_sheet
from sprite_tool.services.ktx2 import find_default_ktx_binary, run_ktx2_export
from sprite_tool.services.optimization import (
    apply_pixel_format,
    downscale_export,
    find_default_pngquant_binary,
    find_default_zopfli_binary,
    optimize_png,
)
from sprite_tool.services.pixi import build_pixi_data
from sprite_tool.ui.widgets import FileDropLineEdit, PreviewPanel, SpriteListWidget, build_browse_field, pil_to_qpixmap

ITEM_KIND_ROLE = Qt.UserRole
ITEM_PATH_ROLE = Qt.UserRole + 1
ITEM_COLOR_ROLE = Qt.UserRole + 2
DEFAULT_SPACER_COLOR = (45, 57, 71, 255)
PACKING_HINTS = {
    "Grid": "Every sprite gets an equal cell, in list order. Simple and predictable.",
    "Basic": "Sprites fill rows from left to right. Compact when sizes are similar.",
    "MaxRects": "Fits sprites of different sizes tightly for the smallest atlas.",
    "Polygon": "Placed like MaxRects, plus outline points per sprite in the JSON.",
}


class GenerateExportDialog(QDialog):
    """Export summary: pick a folder and name, review every file that will be written."""

    def __init__(self, parent: QWidget, folder: str, name: str, plan, tab) -> None:
        super().__init__(parent)
        self.setWindowTitle("Export Atlas")
        self.setMinimumWidth(640)
        self._plan = plan

        self.folder_edit = QLineEdit(folder)
        self.folder_edit.setPlaceholderText("Where should the files be saved?")
        self.name_edit = QLineEdit(name)
        self.name_edit.setPlaceholderText("e.g. characters")
        browse_button = QPushButton("Choose Folder...")
        browse_button.clicked.connect(self._select_folder)

        title = QLabel("Save your atlas")
        title.setObjectName("dialogTitle")
        intro = QLabel("Pick a folder and a name, then tick only the files you want. Your choices are remembered.")
        intro.setObjectName("hint")
        intro.setWordWrap(True)

        folder_row = QWidget()
        folder_layout = QHBoxLayout(folder_row)
        folder_layout.setContentsMargins(0, 0, 0, 0)
        folder_layout.addWidget(self.folder_edit, 1)
        folder_layout.addWidget(browse_button)

        form = QFormLayout()
        form.setSpacing(10)
        form.addRow("Folder", folder_row)
        form.addRow("Name", self.name_edit)

        what_box = QGroupBox("What do you want to export?")
        what = QVBoxLayout(what_box)
        what.setSpacing(6)
        png_box = QCheckBox("Atlas image (PNG)  - always created")
        png_box.setChecked(True)
        png_box.setEnabled(False)
        self.json_cb = self._bound_checkbox("JSON atlas data  (PixiJS / generic)", tab.export_json_checkbox)
        self.godot_tex_cb = self._bound_checkbox("Godot: AtlasTexture .tres for each sprite", tab.godot_atlas_textures_checkbox)
        self.godot_frames_cb = self._bound_checkbox("Godot: SpriteFrames .tres for AnimatedSprite2D", tab.godot_sprite_frames_checkbox)
        self.ktx2_cb = self._bound_checkbox("KTX2 GPU texture  (needs the ktx tool)", tab.export_ktx2_checkbox)
        for widget in (png_box, self.json_cb, self.godot_tex_cb, self.godot_frames_cb, self.ktx2_cb):
            what.addWidget(widget)

        self.godot_project_edit = QLineEdit(tab.godot_project_edit.text())
        self.godot_project_edit.setPlaceholderText("Folder containing project.godot")
        self.godot_project_edit.textChanged.connect(tab.godot_project_edit.setText)
        self.godot_project_edit.textChanged.connect(lambda _text: self.refresh())
        project_browse = QPushButton("Browse")
        project_browse.clicked.connect(self._select_godot_project)
        self.godot_fps_spin = QSpinBox()
        self.godot_fps_spin.setRange(1, 240)
        self.godot_fps_spin.setSuffix(" fps")
        self.godot_fps_spin.setValue(tab.godot_fps_spin.value())
        self.godot_fps_spin.valueChanged.connect(tab.godot_fps_spin.setValue)
        self.godot_box = QWidget()
        godot_form = QFormLayout(self.godot_box)
        godot_form.setContentsMargins(24, 4, 0, 0)
        project_row = QWidget()
        project_layout = QHBoxLayout(project_row)
        project_layout.setContentsMargins(0, 0, 0, 0)
        project_layout.addWidget(self.godot_project_edit, 1)
        project_layout.addWidget(project_browse)
        godot_form.addRow("Godot project", project_row)
        godot_form.addRow("Animation speed", self.godot_fps_spin)
        what.addWidget(self.godot_box)

        self.files_label = QLabel()
        self.files_label.setTextFormat(Qt.RichText)
        self.files_label.setWordWrap(True)
        self.files_label.setObjectName("exportFiles")
        self.notes_label = QLabel()
        self.notes_label.setTextFormat(Qt.RichText)
        self.notes_label.setWordWrap(True)
        self.notes_label.setObjectName("exportNotes")
        self.problems_label = QLabel()
        self.problems_label.setTextFormat(Qt.RichText)
        self.problems_label.setWordWrap(True)
        self.problems_label.setObjectName("exportProblems")

        cancel_button = QPushButton("Cancel")
        self.ok_button = QPushButton("Export")
        self.ok_button.setObjectName("primaryButton")
        self.ok_button.setDefault(True)
        cancel_button.clicked.connect(self.reject)
        self.ok_button.clicked.connect(self.accept)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(cancel_button)
        buttons.addWidget(self.ok_button)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.addWidget(title)
        layout.addWidget(intro)
        layout.addLayout(form)
        layout.addWidget(what_box)
        layout.addWidget(self.files_label)
        layout.addWidget(self.notes_label)
        layout.addWidget(self.problems_label)
        layout.addLayout(buttons)

        self.setStyleSheet(
            """
            QLabel#dialogTitle { font-size: 16px; font-weight: 600; }
            QLabel#hint { color: #8fa4b8; font-size: 12px; }
            QLabel#exportFiles { background: #17212b; border: 1px solid #283646; border-radius: 8px; padding: 10px 12px; }
            QLabel#exportNotes { color: #e0b84a; }
            QLabel#exportProblems { color: #ff7b72; }
            QPushButton#primaryButton { background: #2f6fed; color: white; border: 0; border-radius: 6px; padding: 8px 20px; font-weight: 600; }
            QPushButton#primaryButton:hover { background: #4682f4; }
            QPushButton#primaryButton:disabled { background: #2b3a4a; color: #7b8a99; }
            """
        )
        self.folder_edit.textChanged.connect(self.refresh)
        self.name_edit.textChanged.connect(self.refresh)
        self.refresh()

    def _fit_height(self) -> None:
        self.layout().activate()
        self.resize(self.width(), max(self.minimumSizeHint().height(), self.layout().totalHeightForWidth(self.width())))

    def _bound_checkbox(self, text: str, state: QCheckBox) -> QCheckBox:
        # Mirrors a checkbox kept on the tab so the choice is remembered after the dialog closes.
        box = QCheckBox(text)
        box.setChecked(state.isChecked())
        box.toggled.connect(state.setChecked)
        box.toggled.connect(lambda _checked: self.refresh())
        return box

    def _select_godot_project(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Select Godot Project Folder", self.godot_project_edit.text().strip())
        if path:
            self.godot_project_edit.setText(path)

    def _select_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Choose Output Folder", self.folder_edit.text().strip())
        if path:
            self.folder_edit.setText(path)

    def refresh(self) -> None:
        self.godot_box.setVisible(self.godot_tex_cb.isChecked() or self.godot_frames_cb.isChecked())
        files, problems, notes = self._plan(self.folder_edit.text(), self.name_edit.text())
        if files:
            rows = "".join(
                f"<tr><td style='padding-right:14px'><b>{escape(label)}</b></td><td>{escape(path)}</td></tr>"
                for label, path in files
            )
            self.files_label.setText(f"<div style='margin-bottom:6px'>Will be created:</div><table>{rows}</table>")
        else:
            self.files_label.setText("Nothing to export yet.")
        self.notes_label.setText("<br>".join("&#9888; " + escape(note) for note in notes))
        self.notes_label.setVisible(bool(notes))
        self.problems_label.setText("<br>".join("&#10005; " + escape(problem) for problem in problems))
        self.problems_label.setVisible(bool(problems))
        self.ok_button.setEnabled(not problems)
        if self.isVisible():
            # Grow or shrink with the content (Godot rows and messages appear/disappear).
            QTimer.singleShot(0, self._fit_height)

    def values(self) -> tuple[str, str]:
        return self.folder_edit.text().strip(), self.name_edit.text().strip()


class ClickableImageLabel(QLabel):
    def __init__(self, on_pick) -> None:
        super().__init__()
        self.on_pick = on_pick
        self._image: Optional[Image.Image] = None
        self._pixmap_w = 0
        self._pixmap_h = 0
        self.setMinimumSize(280, 220)
        self.setAlignment(Qt.AlignCenter)
        self.setStyleSheet("background: #10151c; border: 1px solid #32414b;")

    def set_image(self, image: Image.Image) -> None:
        self._image = image.convert("RGBA")
        pixmap = pil_to_qpixmap(self._image)
        scaled = pixmap.scaled(420, 320, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self._pixmap_w = scaled.width()
        self._pixmap_h = scaled.height()
        self.setPixmap(scaled)

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if self._image is None or self.pixmap() is None:
            return
        offset_x = (self.width() - self._pixmap_w) / 2
        offset_y = (self.height() - self._pixmap_h) / 2
        x = event.pos().x() - offset_x
        y = event.pos().y() - offset_y
        if x < 0 or y < 0 or x >= self._pixmap_w or y >= self._pixmap_h:
            return
        source_x = max(0, min(self._image.width - 1, round(x * self._image.width / self._pixmap_w)))
        source_y = max(0, min(self._image.height - 1, round(y * self._image.height / self._pixmap_h)))
        self.on_pick(self._image.getpixel((source_x, source_y)))


class SpacerColorDialog(QDialog):
    def __init__(self, parent: QWidget, initial: tuple[int, int, int, int]) -> None:
        super().__init__(parent)
        self.setWindowTitle("Set Spacer Color")
        self.color_edit = QLineEdit(f"#{initial[0]:02X}{initial[1]:02X}{initial[2]:02X}")
        self.alpha_spin = QSpinBox()
        self.alpha_spin.setRange(0, 255)
        self.alpha_spin.setValue(initial[3])
        self.transparent_checkbox = QCheckBox("Transparent")
        self.transparent_checkbox.toggled.connect(lambda checked: self.alpha_spin.setValue(0 if checked else max(1, self.alpha_spin.value())))
        pick_button = QPushButton("Pick")
        pick_button.clicked.connect(self.pick_color)

        form = QFormLayout()
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.addWidget(self.color_edit)
        row_layout.addWidget(pick_button)
        form.addRow("Color", row)
        form.addRow("Alpha", self.alpha_spin)
        form.addRow("", self.transparent_checkbox)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def pick_color(self) -> None:
        color = QColorDialog.getColor(self._current_qcolor(), self, "Pick Spacer Color", QColorDialog.DontUseNativeDialog | QColorDialog.ShowAlphaChannel)
        if color.isValid():
            self.color_edit.setText(color.name().upper())
            self.alpha_spin.setValue(color.alpha())

    def _current_qcolor(self) -> QColor:
        color = QColor(self.color_edit.text().strip())
        if not color.isValid():
            color = QColor("#2D3947")
        color.setAlpha(self.alpha_spin.value())
        return color

    def value(self) -> tuple[int, int, int, int]:
        color = QColor(self.color_edit.text().strip())
        if not color.isValid():
            raise ValueError("Enter a valid color such as #2D3947.")
        return color.red(), color.green(), color.blue(), self.alpha_spin.value()


class BackgroundColorDialog(QDialog):
    def __init__(
        self,
        parent: QWidget,
        title: str,
        files: List[str],
        initial_rgb: tuple[int, int, int],
        initial_tolerance: int,
        replacement: Optional[tuple[int, int, int, int]] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.files = files
        self.replace_enabled = replacement is not None
        self.sprite_combo = QComboBox()
        self.sprite_combo.addItems([os.path.basename(path) for path in files])
        self.sprite_combo.currentIndexChanged.connect(self.load_current_sprite)
        random_button = QPushButton("Random Sprite")
        random_button.clicked.connect(self.pick_random_sprite)
        self.preview_label = ClickableImageLabel(self.set_source_from_pixel)
        self.source_edit = QLineEdit(f"#{initial_rgb[0]:02X}{initial_rgb[1]:02X}{initial_rgb[2]:02X}")
        self.tolerance_spin = QSpinBox()
        self.tolerance_spin.setRange(0, 255)
        self.tolerance_spin.setValue(initial_tolerance)
        self.target_edit = QLineEdit("#2D3947")
        self.target_alpha_spin = QSpinBox()
        self.target_alpha_spin.setRange(0, 255)
        self.target_alpha_spin.setValue(255)
        if replacement is not None:
            self.target_edit.setText(f"#{replacement[0]:02X}{replacement[1]:02X}{replacement[2]:02X}")
            self.target_alpha_spin.setValue(replacement[3])

        source_pick = QPushButton("Pick Source")
        source_pick.clicked.connect(lambda: self.pick_into(self.source_edit, None))
        target_pick = QPushButton("Pick Target")
        target_pick.clicked.connect(lambda: self.pick_into(self.target_edit, self.target_alpha_spin))

        form = QFormLayout()
        form.addRow("Sprite", self._row(self.sprite_combo, random_button))
        form.addRow("Preview", self.preview_label)
        form.addRow("Source Color", self._row(self.source_edit, source_pick))
        form.addRow("Tolerance", self.tolerance_spin)
        if self.replace_enabled:
            form.addRow("Replacement Color", self._row(self.target_edit, target_pick))
            form.addRow("Replacement Alpha", self.target_alpha_spin)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)
        self.load_current_sprite()

    def _row(self, edit: QLineEdit, button: QPushButton) -> QWidget:
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(edit)
        layout.addWidget(button)
        return widget

    def load_current_sprite(self) -> None:
        if not self.files:
            return
        with Image.open(self.files[self.sprite_combo.currentIndex()]) as opened:
            self.preview_label.set_image(opened.convert("RGBA"))

    def pick_random_sprite(self) -> None:
        if self.files:
            self.sprite_combo.setCurrentIndex(random.randrange(len(self.files)))

    def set_source_from_pixel(self, rgba: tuple[int, int, int, int]) -> None:
        self.source_edit.setText(f"#{rgba[0]:02X}{rgba[1]:02X}{rgba[2]:02X}")

    def pick_into(self, edit: QLineEdit, alpha_spin: Optional[QSpinBox]) -> None:
        seed = QColor(edit.text().strip())
        if not seed.isValid():
            seed = QColor("#000000")
        if alpha_spin is not None:
            seed.setAlpha(alpha_spin.value())
        options = QColorDialog.DontUseNativeDialog
        if alpha_spin is not None:
            options |= QColorDialog.ShowAlphaChannel
        color = QColorDialog.getColor(seed, self, "Pick Color", options)
        if color.isValid():
            edit.setText(color.name().upper())
            if alpha_spin is not None:
                alpha_spin.setValue(color.alpha())

    def values(self) -> tuple[tuple[int, int, int], int, Optional[tuple[int, int, int, int]]]:
        source = QColor(self.source_edit.text().strip())
        if not source.isValid():
            raise ValueError("Enter a valid source color such as #000000.")
        replacement = None
        if self.replace_enabled:
            target = QColor(self.target_edit.text().strip())
            if not target.isValid():
                raise ValueError("Enter a valid replacement color such as #2D3947.")
            replacement = (target.red(), target.green(), target.blue(), self.target_alpha_spin.value())
        return (source.red(), source.green(), source.blue()), self.tolerance_spin.value(), replacement


class GenerateSheetTab(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.files_list = SpriteListWidget(self.add_files_from_drop)
        self.export_folder = ""
        self.export_name = "sheet"
        # These hold the choices made in the Export dialog (remembered between exports).
        # They are never added to the settings UI.
        self.export_json_checkbox = QCheckBox("Export JSON atlas data")
        self.export_json_checkbox.setChecked(True)
        self.godot_project_edit = FileDropLineEdit("dir")
        self.godot_project_edit.setPlaceholderText("Folder containing project.godot")
        self.godot_atlas_textures_checkbox = QCheckBox("AtlasTexture .tres for each sprite")
        self.godot_sprite_frames_checkbox = QCheckBox("SpriteFrames .tres for AnimatedSprite2D")
        self.godot_fps_spin = self._spin(1, 240, 12)
        self.godot_fps_spin.setSuffix(" fps")
        self.packing_hint = self._hint("")
        self.guide_label = QLabel()
        self.guide_label.setObjectName("guideBanner")
        self.guide_label.setTextFormat(Qt.RichText)
        self.guide_label.setWordWrap(True)
        self.name_pattern_edit = QLineEdit("{stem}.png")
        self.packing_combo = QComboBox()
        self.packing_combo.addItems(PACKING_ALGORITHMS)
        self.columns_spin = self._spin(0, 9999, 0)
        self.columns_spin.setSpecialValueText("Auto")
        self.scale_spin = self._spin(1, 800, 100)
        self.scale_spin.setSuffix(" %")
        self.downscale_spin = self._spin(1, 100, 100)
        self.downscale_spin.setSuffix(" %")
        self.shape_padding_spin = self._spin(0, 4096, 0)
        self.shape_padding_spin.setSuffix(" px")
        self.border_padding_spin = self._spin(0, 4096, 0)
        self.border_padding_spin.setSuffix(" px")
        self.trim_checkbox = QCheckBox("Trim transparent bounds")
        self.pot_checkbox = QCheckBox("Use power-of-two sheet size")
        self.export_ktx2_checkbox = QCheckBox("KTX2 Export")
        self.use_ktx2_in_json_checkbox = QCheckBox("Use KTX2 in JSON meta.image")
        self.generate_mipmaps_checkbox = QCheckBox("Generate mipmaps")
        self.mipmap_filter_combo = QComboBox()
        self.mipmap_filter_combo.addItems(MIPMAP_FILTERS)
        self.ktx2_mode_combo = QComboBox()
        self.ktx2_mode_combo.addItems(KTX2_MODES)
        self.ktx_path_edit = FileDropLineEdit("file")
        self.ktx_path_edit.setText(find_default_ktx_binary())
        self.data_format_combo = QComboBox()
        self.data_format_combo.addItems(JSON_FORMATS)
        self.pixel_format_combo = QComboBox()
        self.pixel_format_combo.addItems(PIXEL_FORMATS)
        self.pngquant_checkbox = QCheckBox("Use pngquant")
        self.pngquant_path_edit = FileDropLineEdit("file")
        self.pngquant_path_edit.setText(find_default_pngquant_binary())
        self.pngquant_quality_min_spin = self._spin(0, 100, 65)
        self.pngquant_quality_max_spin = self._spin(0, 100, 95)
        self.pngquant_speed_spin = self._spin(1, 11, 3)
        self.zopfli_checkbox = QCheckBox("Use Zopfli")
        self.zopfli_path_edit = FileDropLineEdit("file")
        self.zopfli_path_edit.setText(find_default_zopfli_binary())
        self.zopfli_iterations_spin = self._spin(1, 500, 15)
        self.preview = PreviewPanel("Atlas Preview")
        self.preview.set_reorder_callback(self.reorder_from_preview)
        self.preview.set_selection_callback(self.handle_preview_selection)
        self._preview_frame_rows: List[int] = []
        self._preview_region_rows: List[int] = []
        self.atlas_background: Optional[tuple[int, int, int, int]] = None
        self.show_checker_background = True
        self.background_key_rgb: Optional[tuple[int, int, int]] = None
        self.background_key_tolerance = 8
        self.background_replacement: Optional[tuple[int, int, int, int]] = None
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self._build_ui()
        self._connect_signals()

    def _spin(self, minimum: int, maximum: int, value: int) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(minimum, maximum)
        spin.setValue(value)
        return spin

    def _build_ui(self) -> None:
        self.setDockNestingEnabled(True)
        self.setDockOptions(
            QMainWindow.AllowNestedDocks
            | QMainWindow.AllowTabbedDocks
            | QMainWindow.AnimatedDocks
        )
        center = QWidget()
        center_layout = QVBoxLayout(center)
        center_layout.setContentsMargins(0, 0, 0, 0)
        center_layout.setSpacing(0)
        center_layout.addWidget(self.guide_label)
        center_layout.addWidget(self.preview, 1)
        self.setCentralWidget(center)

        source_dock = QDockWidget("Sources", self)
        source_dock.setFeatures(QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetFloatable)
        source_dock.setWidget(self._build_source_panel())
        self.addDockWidget(Qt.LeftDockWidgetArea, source_dock)

        settings_dock = QDockWidget("Settings", self)
        settings_dock.setFeatures(QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetFloatable)
        settings_dock.setWidget(self._build_settings_panel())
        self.addDockWidget(Qt.RightDockWidgetArea, settings_dock)

        console_dock = QDockWidget("Console", self)
        console_dock.setFeatures(QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetFloatable)
        console_dock.setWidget(self.log)
        self.addDockWidget(Qt.BottomDockWidgetArea, console_dock)
        console_dock.hide()

        source_dock.setMinimumWidth(320)
        settings_dock.setMinimumWidth(340)
        console_dock.setMinimumHeight(140)
        self.resizeDocks([source_dock, settings_dock], [300, 400], Qt.Horizontal)

        toolbar = QToolBar("Tab Actions", self)
        toolbar.setMovable(True)
        self.addToolBar(Qt.TopToolBarArea, toolbar)
        export_button = self._toolbar_button("Export...")
        export_button.setObjectName("primaryButton")
        export_button.clicked.connect(self.open_export_dialog)
        refresh_button = self._toolbar_button("Refresh Preview")
        refresh_button.clicked.connect(self.update_preview)
        tools_button = self._build_tools_button()
        toggle_console = self._toolbar_button("Console")
        toggle_console.clicked.connect(lambda: console_dock.setVisible(not console_dock.isVisible()))
        toolbar.addWidget(export_button)
        toolbar.addWidget(refresh_button)
        toolbar.addSeparator()
        toolbar.addWidget(tools_button)
        toolbar.addWidget(toggle_console)

        self.setStyleSheet(
            """
            QDockWidget { color: #eef3f8; font-weight: 600; }
            QDockWidget::title {
                background: #19232d;
                padding: 8px 10px;
                text-align: left;
                border-bottom: 1px solid #32414b;
            }
            QToolBar { background: #19232d; border-bottom: 1px solid #32414b; spacing: 8px; padding: 6px 8px; }
            QToolButton#toolbarButton {
                background: #253340; color: #eef3f8; border: 1px solid #32414b;
                border-radius: 6px; padding: 6px 12px; font-weight: 500;
            }
            QToolButton#toolbarButton:hover { background: #32414b; }
            QToolButton#primaryButton {
                background: #2f6fed; color: white; border: 0; border-radius: 6px;
                padding: 7px 18px; font-weight: 600;
            }
            QToolButton#primaryButton:hover { background: #4682f4; }
            QLabel#guideBanner {
                background: #1b2b3d; color: #dbe7f3; border-bottom: 1px solid #2d3f55;
                padding: 10px 16px; font-size: 13px;
            }
            QLabel#hint { color: #8fa4b8; font-size: 11px; }
            QGroupBox {
                border: 1px solid #2d3947; border-radius: 8px; margin-top: 14px;
                padding: 14px 10px 10px 10px; background: #17212b; color: #edf3f8; font-weight: 600;
            }
            QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 6px; color: #9fb6cc; }
            QGroupBox QLabel, QGroupBox QCheckBox { font-weight: 400; }
            QPushButton {
                background: #253340; color: #eef3f8; border: 1px solid #32414b;
                border-radius: 6px; padding: 6px 12px; font-weight: 500;
            }
            QPushButton:hover { background: #32414b; }
            QTabBar::tab { padding: 8px 14px; color: #9fb6cc; }
            QTabBar::tab:selected { color: #ffffff; border-bottom: 2px solid #2f6fed; }
            QTextEdit { font-family: monospace; }
            """
        )

    def _toolbar_button(self, text: str) -> QToolButton:
        button = QToolButton(self)
        button.setObjectName("toolbarButton")
        button.setText(text)
        return button

    def _build_tools_button(self) -> QToolButton:
        menu = QMenu("Tools", self)
        actions = [
            ("Toggle Checker Background", self.toggle_checker_background),
            ("Set Atlas Background Color", self.pick_atlas_background),
            ("Clear Atlas Background", self.clear_atlas_background),
            ("Set Spacer Color", self.pick_spacer_color),
            ("Remove Sprite Background", self.configure_remove_background),
            ("Replace Sprite Background", self.configure_replace_background),
            ("Reset Sprite Background Tools", self.reset_sprite_background_tools),
        ]
        for label, callback in actions:
            action = QAction(label, self)
            action.triggered.connect(lambda _checked=False, handler=callback: handler())
            menu.addAction(action)
        button = self._toolbar_button("Tools")
        button.setMenu(menu)
        button.setPopupMode(QToolButton.InstantPopup)
        return button

    def _build_source_panel(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        files_box = QGroupBox("Source Sprites")
        files_layout = QVBoxLayout(files_box)
        file_buttons = QHBoxLayout()
        edit_buttons = QHBoxLayout()
        add_button = QPushButton("Add Files")
        spacer_button = QPushButton("Add Spacer")
        remove_button = QPushButton("Remove Selected")
        clear_button = QPushButton("Clear List")
        add_button.clicked.connect(self.add_files)
        spacer_button.clicked.connect(self.add_spacer)
        remove_button.clicked.connect(self.remove_selected_items)
        clear_button.clicked.connect(self.clear_files)
        file_buttons.addWidget(add_button)
        file_buttons.addWidget(clear_button)
        edit_buttons.addWidget(spacer_button)
        edit_buttons.addWidget(remove_button)
        files_layout.addLayout(file_buttons)
        files_layout.addLayout(edit_buttons)
        files_layout.addWidget(self.files_list)
        files_layout.addWidget(self._hint("Drag rows to reorder. A spacer adds an empty (optionally colored) cell to group sprites."))
        layout.addWidget(files_box)
        return widget

    def _hint(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("hint")
        label.setWordWrap(True)
        return label

    def _field(self, widget: QWidget, hint: str = "") -> QWidget:
        # A control with an always-visible one-line explanation underneath it.
        if not hint:
            return widget
        widget.setToolTip(hint)
        box = QWidget()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        layout.addWidget(widget)
        layout.addWidget(self._hint(hint))
        return box

    def _group(self, title: str):
        box = QGroupBox(title)
        form = QFormLayout(box)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        form.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(10)
        return box, form

    def _scroll(self, widget: QWidget) -> QScrollArea:
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.NoFrame)
        area.setWidget(widget)
        return area

    def _tab_page(self, intro: str, *boxes: QWidget) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(10, 12, 10, 12)
        layout.setSpacing(12)
        layout.addWidget(self._hint(intro))
        for box in boxes:
            layout.addWidget(box)
        layout.addStretch(1)
        return page

    def _build_settings_panel(self) -> QWidget:
        tabs = QTabWidget()
        tabs.setDocumentMode(True)
        tabs.addTab(self._scroll(self._build_layout_tab()), "1  Layout")
        tabs.addTab(self._scroll(self._build_export_tab()), "2  Export")
        tabs.addTab(self._scroll(self._build_optimize_tab()), "3  Optimize")
        return tabs

    def _build_layout_tab(self) -> QWidget:
        packing_box, packing = self._group("Packing")
        packing.addRow("Method", self.packing_combo)
        packing.addRow("", self.packing_hint)
        packing.addRow("Columns", self._field(self.columns_spin, "Auto picks a near-square grid."))
        packing.addRow("Space between", self._field(self.shape_padding_spin, "Pixels between sprites. 1-2 px avoids edge bleeding when scaled."))
        packing.addRow("Edge margin", self._field(self.border_padding_spin, "Empty pixels around the whole atlas."))
        packing.addRow("", self.pot_checkbox)

        sprite_box, sprites = self._group("Sprites")
        sprites.addRow("Scale", self._field(self.scale_spin, "Resizes every source sprite before packing."))
        sprites.addRow("", self.trim_checkbox)
        sprites.addRow("Frame names", self._field(self.name_pattern_edit, "Variables: {stem} {index} {name} {ext}. For Godot animations name sprites walk_0, walk_1, ..."))
        return self._tab_page("Decide how sprites are arranged. The preview updates as you change things.", packing_box, sprite_box)

    def _build_export_tab(self) -> QWidget:
        size_box, size = self._group("Final size")
        size.addRow("Downscale", self._field(self.downscale_spin, "Shrinks the finished atlas and all frame coordinates. 100% keeps full size."))

        json_box, json_form = self._group("JSON atlas data")
        json_form.addRow("Layout", self._field(self.data_format_combo, "hash: frames keyed by name. array: list of frames."))

        ktx_box, ktx = self._group("KTX2 (GPU texture)")
        ktx.addRow("Quality", self._field(self.ktx2_mode_combo, "UASTC: higher quality. ETC1S: smaller files."))
        ktx.addRow("", self.generate_mipmaps_checkbox)
        ktx.addRow("Mipmap filter", self.mipmap_filter_combo)
        ktx.addRow("", self.use_ktx2_in_json_checkbox)
        ktx.addRow("ktx tool", self._field(self.ktx_path_edit, "Needs KTX-Software installed (click ? for downloads)."))

        return self._tab_page("How exported files look. Choose which files to create when you press Export...", size_box, json_box, ktx_box)

    def _build_optimize_tab(self) -> QWidget:
        quality_box, quality = self._group("Color")
        quality.addRow("Pixel format", self._field(self.pixel_format_combo, "RGBA8888 is full quality. Lower formats shrink color depth to simulate mobile GPU formats."))

        pngquant_box, pngquant = self._group("pngquant (smaller PNG)")
        pngquant.addRow(self.pngquant_checkbox)
        self.pngquant_path_row = build_browse_field(self.pngquant_path_edit, "Browse", lambda: self._select_tool_path(self.pngquant_path_edit), "")
        pngquant.addRow("Program", self.pngquant_path_row)
        pngquant.addRow("Min quality", self.pngquant_quality_min_spin)
        pngquant.addRow("Max quality", self.pngquant_quality_max_spin)
        pngquant.addRow("Speed", self._field(self.pngquant_speed_spin, "1 is slowest and best, 11 is fastest."))

        zopfli_box, zopfli = self._group("Zopfli (lossless squeeze)")
        zopfli.addRow(self.zopfli_checkbox)
        self.zopfli_path_row = build_browse_field(self.zopfli_path_edit, "Browse", lambda: self._select_tool_path(self.zopfli_path_edit), "")
        zopfli.addRow("Program", self.zopfli_path_row)
        zopfli.addRow("Iterations", self._field(self.zopfli_iterations_spin, "More iterations: smaller file, slower export."))

        self._pngquant_dependents = [self.pngquant_path_row, self.pngquant_quality_min_spin, self.pngquant_quality_max_spin, self.pngquant_speed_spin]
        self._zopfli_dependents = [self.zopfli_path_row, self.zopfli_iterations_spin]
        return self._tab_page("Optional. Leave everything off for a plain PNG. These tools must be installed separately.", quality_box, pngquant_box, zopfli_box)

    def _sync_target_ui(self) -> None:
        for widget in self._pngquant_dependents:
            widget.setEnabled(self.pngquant_checkbox.isChecked())
        for widget in self._zopfli_dependents:
            widget.setEnabled(self.zopfli_checkbox.isChecked())

    def _update_packing_hint(self) -> None:
        self.packing_hint.setText(PACKING_HINTS.get(self.packing_combo.currentText(), ""))

    def _select_godot_project(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Select Godot Project Folder", self.godot_project_edit.text().strip())
        if path:
            self.godot_project_edit.setText(path)

    @staticmethod
    def _find_godot_project(start: str) -> str:
        current = os.path.abspath(start)
        while True:
            if os.path.isfile(os.path.join(current, "project.godot")):
                return current
            parent = os.path.dirname(current)
            if parent == current:
                return ""
            current = parent

    def _update_guide(self, message: str = "") -> None:
        count = len(self._collect_real_file_paths())
        if message:
            text = f"<b>Something needs attention:</b> {escape(message)}"
        elif count == 0:
            text = "<b>Step 1</b> &nbsp;Add your sprites: click <b>Add Files</b> or drag images into the Sources list."
        else:
            text = (
                f"<b>{count} sprite{'s' if count != 1 else ''} ready.</b> &nbsp;"
                "<b>Step 2</b> adjust Layout in Settings, check the preview. &nbsp;"
                "<b>Step 3</b> press <b>Export...</b>"
            )
        self.guide_label.setText(text)

    def _tool_available(self, path: str) -> bool:
        path = path.strip()
        return bool(path) and bool(shutil.which(path) or os.path.isfile(path))

    def _export_plan(self, folder: str, name: str):
        # Returns (files to create, blocking problems, non-blocking notes) for the dialog.
        files: List[tuple[str, str]] = []
        problems: List[str] = []
        notes: List[str] = []
        folder = folder.strip()
        name = name.strip()
        if not self._collect_real_file_paths():
            problems.append("Add at least one sprite first.")
        if not folder:
            problems.append("Choose an output folder.")
        if not name:
            problems.append("Enter a file name.")
        elif re.search(r'[\\/:*?"<>|]', name):
            problems.append('The name cannot contain \\ / : * ? " < > |')
        if not folder or not name:
            return files, problems, notes

        base = os.path.join(folder, name)
        files.append(("Atlas PNG", base + ".png"))
        if self.export_json_checkbox.isChecked():
            files.append(("PixiJS JSON", base + ".json"))
        if self.export_ktx2_checkbox.isChecked():
            files.append(("KTX2", base + ".ktx2"))
            if not self._tool_available(self.ktx_path_edit.text()):
                problems.append("KTX2 is ticked but the ktx tool was not found. Set its path in Settings > Export.")
        if self.godot_atlas_textures_checkbox.isChecked() or self.godot_sprite_frames_checkbox.isChecked():
            project = self.godot_project_edit.text().strip()
            kinds = []
            if self.godot_atlas_textures_checkbox.isChecked():
                kinds.append("AtlasTexture per sprite")
            if self.godot_sprite_frames_checkbox.isChecked():
                kinds.append("SpriteFrames")
            if not project:
                problems.append("Choose the Godot project folder.")
            elif not os.path.isfile(os.path.join(project, "project.godot")):
                problems.append("The Godot project folder does not contain project.godot.")
            else:
                try:
                    res_path_for(base + ".png", project)
                except ValueError:
                    problems.append("For Godot, the output folder must be inside the project folder.")
            files.append(("Godot", f"{base}_godot/  ({', '.join(kinds)})"))
        if self.pngquant_checkbox.isChecked() and not self._tool_available(self.pngquant_path_edit.text()):
            problems.append("pngquant is on but was not found. Set its path in Settings > Optimize.")
        if self.zopfli_checkbox.isChecked() and not self._tool_available(self.zopfli_path_edit.text()):
            problems.append("Zopfli is on but zopflipng was not found. Set its path in Settings > Optimize.")
        for label, path in files:
            if os.path.isfile(path):
                notes.append(f"{os.path.basename(path)} already exists and will be replaced.")
        return files, problems, notes


    def _connect_signals(self) -> None:
        self.packing_combo.currentTextChanged.connect(self._update_packing_hint)
        self.packing_combo.currentTextChanged.connect(self.update_preview)
        for checkbox in (self.pngquant_checkbox, self.zopfli_checkbox):
            checkbox.toggled.connect(self._sync_target_ui)
        self.columns_spin.valueChanged.connect(self.update_preview)
        self.scale_spin.valueChanged.connect(self.update_preview)
        self.shape_padding_spin.valueChanged.connect(self.update_preview)
        self.border_padding_spin.valueChanged.connect(self.update_preview)
        self.name_pattern_edit.textChanged.connect(self.update_preview)
        self.trim_checkbox.toggled.connect(self.update_preview)
        self.pot_checkbox.toggled.connect(self.update_preview)
        self.pixel_format_combo.currentTextChanged.connect(self.update_preview)
        self.generate_mipmaps_checkbox.toggled.connect(self._sync_ktx2_ui)
        self.files_list.model().rowsMoved.connect(lambda *_: self.update_preview())
        self._sync_ktx2_ui()
        self._sync_target_ui()
        self._update_packing_hint()
        self.update_preview()

    def _collect_file_paths(self) -> List[Optional[str]]:
        paths: List[Optional[str]] = []
        self._preview_frame_rows = []
        self._preview_region_rows = []
        for row in range(self.files_list.count()):
            item = self.files_list.item(row)
            kind = item.data(ITEM_KIND_ROLE)
            self._preview_region_rows.append(row)
            if kind == "spacer":
                paths.append(None)
                continue
            path = item.data(ITEM_PATH_ROLE) or item.text()
            paths.append(path)
            self._preview_frame_rows.append(row)
        return paths

    def _region_labels(self) -> List[str]:
        # Region labels follow source-list rows, not only exported frames, so
        # spacer cells remain visible and addressable in the preview sidebar.
        labels = []
        sprite_index = 0
        for row in range(self.files_list.count()):
            item = self.files_list.item(row)
            if item.data(ITEM_KIND_ROLE) == "spacer":
                labels.append(f"Spacer {row + 1}")
            else:
                labels.append(f"{os.path.basename(item.data(ITEM_PATH_ROLE) or item.text())} | #{sprite_index}")
                sprite_index += 1
        return labels

    def _collect_cell_backgrounds(self) -> List[Optional[tuple[int, int, int, int]]]:
        backgrounds: List[Optional[tuple[int, int, int, int]]] = []
        for row in range(self.files_list.count()):
            item = self.files_list.item(row)
            if item.data(ITEM_KIND_ROLE) == "spacer":
                backgrounds.append(item.data(ITEM_COLOR_ROLE) or DEFAULT_SPACER_COLOR)
            else:
                backgrounds.append(self.atlas_background)
        return backgrounds

    def _collect_real_file_paths(self) -> List[str]:
        paths = []
        for row in range(self.files_list.count()):
            item = self.files_list.item(row)
            if item.data(ITEM_KIND_ROLE) != "spacer":
                paths.append(item.data(ITEM_PATH_ROLE) or item.text())
        return paths

    def clear_files(self) -> None:
        self.files_list.clear()
        self.update_preview()

    def remove_selected_items(self) -> None:
        for item in self.files_list.selectedItems():
            self.files_list.takeItem(self.files_list.row(item))
        self.update_preview()

    def add_spacer(self) -> None:
        item = QListWidgetItem("[Spacer]")
        item.setData(ITEM_KIND_ROLE, "spacer")
        item.setData(ITEM_COLOR_ROLE, self.atlas_background or DEFAULT_SPACER_COLOR)
        current_row = self._current_source_row()
        insert_row = current_row + 1 if current_row >= 0 else self.files_list.count()
        self.files_list.insertItem(insert_row, item)
        self.files_list.setCurrentItem(item)
        self.update_preview()

    def _current_source_row(self) -> int:
        # Prefer the atlas preview selection over the list current row. This lets
        # Add Spacer insert after the region the user clicked on the atlas.
        selected_preview = self.preview.get_selected_indexes()
        if selected_preview:
            region_index = selected_preview[-1]
            if region_index < len(self._preview_region_rows):
                return self._preview_region_rows[region_index]
        return self.files_list.currentRow()

    def handle_preview_selection(self, selected_regions: List[int]) -> None:
        # Keep the source list and atlas selection aligned for both sprites and
        # spacers. Signal blocking prevents a selection feedback loop.
        self.files_list.blockSignals(True)
        try:
            self.files_list.clearSelection()
            for region_index in selected_regions:
                if region_index < len(self._preview_region_rows):
                    row = self._preview_region_rows[region_index]
                    item = self.files_list.item(row)
                    if item is not None:
                        item.setSelected(True)
                        self.files_list.setCurrentItem(item)
        finally:
            self.files_list.blockSignals(False)

    def add_files(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(self, "Select Sprite Files", "", IMAGE_FILTER)
        if files:
            self.add_files_from_drop(files)

    def add_files_from_drop(self, files: List[str]) -> None:
        existing = {path for path in self._collect_file_paths() if path}
        for file_path in files:
            if file_path not in existing:
                item = QListWidgetItem(os.path.basename(file_path))
                item.setToolTip(file_path)
                item.setData(ITEM_KIND_ROLE, "file")
                item.setData(ITEM_PATH_ROLE, file_path)
                self.files_list.addItem(item)
        if files and not self.export_folder:
            self.export_folder = os.path.dirname(files[0])
        if files and not self.godot_project_edit.text().strip():
            self.godot_project_edit.setText(self._find_godot_project(os.path.dirname(files[0])))
        self.update_preview()

    def toggle_checker_background(self) -> None:
        self.show_checker_background = not self.show_checker_background
        self.update_preview()

    def pick_atlas_background(self) -> None:
        color = self._pick_color(QColor("#2D3947"), "Pick Atlas Background", with_alpha=True)
        if color.isValid():
            self.atlas_background = (color.red(), color.green(), color.blue(), color.alpha())
            self.update_preview()

    def clear_atlas_background(self) -> None:
        self.atlas_background = None
        self.update_preview()

    def pick_spacer_color(self) -> None:
        initial = DEFAULT_SPACER_COLOR
        for row in range(self.files_list.count()):
            item = self.files_list.item(row)
            if item.data(ITEM_KIND_ROLE) == "spacer":
                initial = item.data(ITEM_COLOR_ROLE) or DEFAULT_SPACER_COLOR
                break
        dialog = SpacerColorDialog(self, initial)
        if dialog.exec_() != QDialog.Accepted:
            return
        try:
            rgba = dialog.value()
        except ValueError as exc:
            QMessageBox.warning(self, "Invalid Color", str(exc))
            return
        changed = False
        for row in range(self.files_list.count()):
            item = self.files_list.item(row)
            if item.data(ITEM_KIND_ROLE) == "spacer":
                item.setData(ITEM_COLOR_ROLE, rgba)
                changed = True
        if not changed:
            self.add_spacer()
            self.files_list.currentItem().setData(ITEM_COLOR_ROLE, rgba)
        self.update_preview()

    def configure_remove_background(self) -> None:
        files = self._collect_real_file_paths()
        if not files:
            QMessageBox.warning(self, "No Sprites", "Add at least one sprite before using background removal.")
            return
        initial = self.background_key_rgb or (0, 0, 0)
        dialog = BackgroundColorDialog(self, "Remove Sprite Background", files, initial, self.background_key_tolerance)
        if dialog.exec_() != QDialog.Accepted:
            return
        try:
            source_rgb, tolerance, _replacement = dialog.values()
        except ValueError as exc:
            QMessageBox.warning(self, "Invalid Color", str(exc))
            return
        self.background_key_rgb = source_rgb
        self.background_key_tolerance = tolerance
        self.background_replacement = None
        self.update_preview()

    def configure_replace_background(self) -> None:
        files = self._collect_real_file_paths()
        if not files:
            QMessageBox.warning(self, "No Sprites", "Add at least one sprite before replacing backgrounds.")
            return
        initial = self.background_key_rgb or (0, 0, 0)
        replacement = self.background_replacement or (45, 57, 71, 255)
        dialog = BackgroundColorDialog(self, "Replace Sprite Background", files, initial, self.background_key_tolerance, replacement)
        if dialog.exec_() != QDialog.Accepted:
            return
        try:
            source_rgb, tolerance, replacement_rgba = dialog.values()
        except ValueError as exc:
            QMessageBox.warning(self, "Invalid Color", str(exc))
            return
        self.background_key_rgb = source_rgb
        self.background_key_tolerance = tolerance
        self.background_replacement = replacement_rgba
        self.update_preview()

    def reset_sprite_background_tools(self) -> None:
        self.background_key_rgb = None
        self.background_replacement = None
        self.update_preview()

    def _pick_color(self, initial: QColor, title: str, with_alpha: bool = False) -> QColor:
        # Native color dialogs are inconsistent across Linux desktops; the Qt
        # dialog keeps palette selection reliable in this tool.
        options = QColorDialog.DontUseNativeDialog
        if with_alpha:
            options |= QColorDialog.ShowAlphaChannel
        return QColorDialog.getColor(initial, self, title, options)

    def _preview_image(self, sheet):
        if not self.show_checker_background:
            return sheet
        tile = 16
        checker = Image.new("RGBA", sheet.size, (36, 43, 52, 255))
        for y in range(0, sheet.height, tile):
            for x in range(0, sheet.width, tile):
                if (x // tile + y // tile) % 2 == 0:
                    checker.paste((64, 76, 89, 255), (x, y, min(x + tile, sheet.width), min(y + tile, sheet.height)))
        return Image.alpha_composite(checker, sheet.convert("RGBA"))

    def _select_save_path(self, target: QLineEdit, file_filter: str) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Select Output Path", target.text().strip(), file_filter)
        if path:
            target.setText(path)

    def _select_tool_path(self, target: QLineEdit) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Select Executable", target.text().strip())
        if path:
            target.setText(path)

    def _build_ktx2_settings(self, enabled: bool, output_path: str) -> Ktx2Settings:
        return Ktx2Settings(
            enabled=enabled,
            output_path=output_path,
            encoder_mode=self.ktx2_mode_combo.currentText(),
            ktx_path=self.ktx_path_edit.text(),
            generate_mipmaps=self.generate_mipmaps_checkbox.isChecked(),
            mipmap_filter=self.mipmap_filter_combo.currentText(),
            use_in_json=self.use_ktx2_in_json_checkbox.isChecked(),
        )

    def _build_optimization_settings(self) -> OptimizationSettings:
        min_quality = min(self.pngquant_quality_min_spin.value(), self.pngquant_quality_max_spin.value())
        max_quality = max(self.pngquant_quality_min_spin.value(), self.pngquant_quality_max_spin.value())
        return OptimizationSettings(
            pixel_format=self.pixel_format_combo.currentText(),
            export_downscale_percent=self.downscale_spin.value(),
            pngquant_enabled=self.pngquant_checkbox.isChecked(),
            pngquant_path=self.pngquant_path_edit.text().strip(),
            pngquant_quality_min=min_quality,
            pngquant_quality_max=max_quality,
            pngquant_speed=self.pngquant_speed_spin.value(),
            zopfli_enabled=self.zopfli_checkbox.isChecked(),
            zopfli_path=self.zopfli_path_edit.text().strip(),
            zopfli_iterations=self.zopfli_iterations_spin.value(),
        )

    def _sync_ktx2_ui(self) -> None:
        self.mipmap_filter_combo.setEnabled(self.generate_mipmaps_checkbox.isChecked())

    def log_message(self, message: str) -> None:
        self.log.append(message)

    def update_preview(self) -> None:
        try:
            file_paths = self._collect_file_paths()
            self._update_guide()
            if not file_paths:
                self.preview.set_preview(None, [], "Waiting for source sprites.", region_labels=[])
                return
            sheet, frames, columns, rows, max_w, max_h, layout_rects = compose_sheet(
                file_paths=file_paths,
                columns_requested=self.columns_spin.value(),
                shape_padding=self.shape_padding_spin.value(),
                border_padding=self.border_padding_spin.value(),
                trim_enabled=self.trim_checkbox.isChecked(),
                pot_enabled=self.pot_checkbox.isChecked(),
                name_pattern=self.name_pattern_edit.text().strip() or "{stem}.png",
                scale_percent=self.scale_spin.value(),
                cell_backgrounds=self._collect_cell_backgrounds(),
                transparent_rgb=self.background_key_rgb,
                color_tolerance=self.background_key_tolerance,
                replacement_rgba=self.background_replacement,
                packing_algorithm=self.packing_combo.currentText(),
                return_cells=True,
            )
            self.preview.set_preview(
                self._preview_image(sheet),
                layout_rects,
                f"{sheet.width}x{sheet.height} px | {self.packing_combo.currentText()} | {columns}x{rows} | cell {max_w}x{max_h}",
                [
                    ("Sprites", str(len(frames))),
                    ("Packing", self.packing_combo.currentText()),
                    ("Cell", f"{max_w} x {max_h}"),
                    ("Scale", f"{self.scale_spin.value()}%"),
                    ("BG Tool", "off" if self.background_key_rgb is None else ("remove" if self.background_replacement is None else "replace")),
                ],
                region_labels=self._region_labels(),
            )
        except Exception as exc:
            self._update_guide(str(exc))
            self.preview.set_preview(
                None,
                [],
                f"Preview error: {exc}",
                [("Status", "Error"), ("Reason", str(exc)), ("Sprites", "-"), ("Grid", "-")],
                region_labels=[],
            )

    def open_export_dialog(self) -> None:
        dialog = GenerateExportDialog(self, self.export_folder, self.export_name, self._export_plan, self)
        if dialog.exec_() != QDialog.Accepted:
            return
        folder, name = dialog.values()
        self.export_folder, self.export_name = folder, name
        base = os.path.join(folder, name)
        godot = None
        if self.godot_atlas_textures_checkbox.isChecked() or self.godot_sprite_frames_checkbox.isChecked():
            godot = GodotSettings(
                enabled=True,
                project_dir=self.godot_project_edit.text().strip(),
                output_dir=f"{base}_godot",
                atlas_textures=self.godot_atlas_textures_checkbox.isChecked(),
                sprite_frames=self.godot_sprite_frames_checkbox.isChecked(),
                fps=self.godot_fps_spin.value(),
            )
        self.export_sheet(
            base + ".png",
            base + ".json" if self.export_json_checkbox.isChecked() else "",
            self.export_ktx2_checkbox.isChecked(),
            base + ".ktx2",
            godot,
        )

    def export_sheet(
        self,
        output_image: str,
        output_json: str,
        export_ktx2: bool,
        output_ktx2: str,
        godot: Optional[GodotSettings] = None,
    ) -> None:
        self.log.clear()
        try:
            if not output_image:
                raise ValueError("You must set the sheet PNG path.")
            if export_ktx2 and not output_ktx2:
                raise ValueError("KTX2 output path is required when KTX2 export is enabled.")
            godot_atlas_res = ""
            if godot is not None and godot.enabled:
                if not godot.project_dir or not godot.output_dir:
                    raise ValueError("Set the Godot project folder and Godot output folder.")
                if not (godot.atlas_textures or godot.sprite_frames):
                    raise ValueError("Select at least one Godot resource type.")
                godot_atlas_res = res_path_for(output_image, godot.project_dir)

            sheet, frames, columns, rows, max_w, max_h = compose_sheet(
                file_paths=self._collect_file_paths(),
                columns_requested=self.columns_spin.value(),
                shape_padding=self.shape_padding_spin.value(),
                border_padding=self.border_padding_spin.value(),
                trim_enabled=self.trim_checkbox.isChecked(),
                pot_enabled=self.pot_checkbox.isChecked(),
                name_pattern=self.name_pattern_edit.text().strip() or "{stem}.png",
                scale_percent=self.scale_spin.value(),
                cell_backgrounds=self._collect_cell_backgrounds(),
                transparent_rgb=self.background_key_rgb,
                color_tolerance=self.background_key_tolerance,
                replacement_rgba=self.background_replacement,
                packing_algorithm=self.packing_combo.currentText(),
            )
            os.makedirs(os.path.dirname(output_image) or ".", exist_ok=True)
            if output_json:
                os.makedirs(os.path.dirname(output_json) or ".", exist_ok=True)
            optimization = self._build_optimization_settings()
            # Export transforms happen after packing: downscale image/frames
            # together, simulate pixel format, then run optional PNG compressors.
            export_sheet, export_frames = downscale_export(sheet, frames, optimization.export_downscale_percent)
            matte_rgb = self.atlas_background[:3] if self.atlas_background is not None else (0, 0, 0)
            export_sheet = apply_pixel_format(export_sheet, optimization.pixel_format, matte_rgb)
            export_sheet.save(output_image)
            for message in optimize_png(output_image, optimization):
                self.log_message(message)

            ktx2 = self._build_ktx2_settings(export_ktx2, output_ktx2)
            if ktx2.enabled:
                command_text = run_ktx2_export(
                    output_image,
                    ktx2.output_path,
                    ktx2.encoder_mode,
                    ktx2.ktx_path,
                    ktx2.generate_mipmaps,
                    ktx2.mipmap_filter,
                )
                self.log_message(f"KTX2 created: {ktx2.output_path}")
                self.log_message(f"ktx: {command_text}")

            if output_json:
                json_image_name = os.path.basename(ktx2.output_path) if ktx2.use_in_json else os.path.basename(output_image)
                pixi_data = build_pixi_data(
                    json_image_name,
                    export_sheet.width,
                    export_sheet.height,
                    export_frames,
                    self.data_format_combo.currentText(),
                    optimization.pixel_format,
                )
                with open(output_json, "w", encoding="utf-8") as handle:
                    json.dump(pixi_data, handle, indent=2)

            if godot is not None and godot.enabled:
                written = export_godot_resources(
                    godot_atlas_res,
                    export_frames,
                    godot.output_dir,
                    os.path.splitext(os.path.basename(output_image))[0],
                    godot.atlas_textures,
                    godot.sprite_frames,
                    godot.fps,
                )
                self.log_message(f"Godot resources: {len(written)} file(s) in {godot.output_dir}")

            self.log_message(f"Sheet created: {output_image}")
            if output_json:
                self.log_message(f"PixiJS JSON created: {output_json}")
                self.log_message(f"JSON format: {self.data_format_combo.currentText()}")
            self.log_message(f"Sprite pattern: {self.name_pattern_edit.text().strip() or '{stem}.png'}")
            self.log_message(f"Layout: {columns} columns x {rows} rows")
            self.log_message(f"Cell size: {max_w}x{max_h}")
            self.log_message(f"Packing: {self.packing_combo.currentText()}")
            self.log_message(f"Prescale: {self.scale_spin.value()}%")
            self.log_message(f"Export downscale: {self.downscale_spin.value()}%")
            self.log_message(f"Pixel format: {self.pixel_format_combo.currentText()}")
            self.log_message(f"Atlas background: {'on' if self.atlas_background is not None else 'transparent'}")
            if self.background_key_rgb is not None:
                mode = "remove" if self.background_replacement is None else "replace"
                self.log_message(f"Sprite background tool: {mode} tolerance {self.background_key_tolerance}")
            self.log_message(f"Trim: {'on' if self.trim_checkbox.isChecked() else 'off'}")
            self.log_message(f"Power of Two: {'on' if self.pot_checkbox.isChecked() else 'off'}")
            self.log_message(f"KTX2: {'on' if ktx2.enabled else 'off'}")
            self.log_message(f"Mipmap: {'on' if ktx2.generate_mipmaps else 'off'}")
            self._show_export_done(output_image, output_json, ktx2.output_path if ktx2.enabled else "", godot)
        except Exception as exc:
            QMessageBox.critical(self, "Export failed", str(exc))
            self.log_message(f"Error: {exc}")

    def _show_export_done(self, image: str, json_path: str, ktx2_path: str, godot: Optional[GodotSettings]) -> None:
        lines = [f"Atlas: {os.path.basename(image)}"]
        if json_path:
            lines.append(f"JSON: {os.path.basename(json_path)}")
        if ktx2_path:
            lines.append(f"KTX2: {os.path.basename(ktx2_path)}")
        if godot is not None and godot.enabled:
            lines.append(f"Godot resources: {os.path.basename(godot.output_dir)}/")
        folder = os.path.dirname(os.path.abspath(image))
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Information)
        box.setWindowTitle("Export complete")
        box.setText("Your atlas is ready.")
        box.setInformativeText("\n".join(lines) + f"\n\nSaved in {folder}")
        open_button = box.addButton("Open Folder", QMessageBox.ActionRole)
        box.addButton("Close", QMessageBox.RejectRole)
        box.exec_()
        if box.clickedButton() is open_button:
            QDesktopServices.openUrl(QUrl.fromLocalFile(folder))

    def reorder_from_preview(self, source_frame: int, target_frame: int, insert_after: bool) -> None:
        if source_frame == target_frame:
            return
        self._collect_file_paths()
        if source_frame >= len(self._preview_region_rows) or target_frame >= len(self._preview_region_rows):
            return

        source_row = self._preview_region_rows[source_frame]
        target_row = self._preview_region_rows[target_frame]
        item = self.files_list.takeItem(source_row)
        if item is None:
            return
        if source_row < target_row:
            target_row -= 1
        insert_row = target_row + (1 if insert_after else 0)
        insert_row = max(0, min(insert_row, self.files_list.count()))
        self.files_list.insertItem(insert_row, item)
        self.files_list.setCurrentItem(item)
        self.update_preview()
