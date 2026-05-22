import json
import os
import random
from typing import List, Optional

from PIL import Image
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (
    QAction,
    QCheckBox,
    QComboBox,
    QColorDialog,
    QDialog,
    QDialogButtonBox,
    QDockWidget,
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
    QSpinBox,
    QTextEdit,
    QToolBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from sprite_tool.constants import IMAGE_FILTER, JSON_FORMATS, KTX2_MODES, MIPMAP_FILTERS, PACKING_ALGORITHMS, PIXEL_FORMATS
from sprite_tool.models import Ktx2Settings, OptimizationSettings
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
from sprite_tool.ui.widgets import FileDropLineEdit, PreviewPanel, SpriteListWidget, build_browse_field, build_help_field, build_help_label, pil_to_qpixmap

ITEM_KIND_ROLE = Qt.UserRole
ITEM_PATH_ROLE = Qt.UserRole + 1
ITEM_COLOR_ROLE = Qt.UserRole + 2
DEFAULT_SPACER_COLOR = (45, 57, 71, 255)


class GenerateExportDialog(QDialog):
    def __init__(self, parent: QWidget, image_path: str, json_path: str, ktx2_path: str, ktx2_enabled: bool) -> None:
        super().__init__(parent)
        self.setWindowTitle("Atlas Export")
        self.output_image_edit = QLineEdit(image_path)
        self.output_json_edit = QLineEdit(json_path)
        self.output_ktx2_edit = QLineEdit(ktx2_path)
        self.ktx2_checkbox = QCheckBox("Export KTX2")
        self.ktx2_checkbox.setChecked(ktx2_enabled)

        image_browse = QPushButton("Browse")
        image_browse.clicked.connect(lambda: self._select_file(self.output_image_edit, "PNG (*.png)"))
        json_browse = QPushButton("Browse")
        json_browse.clicked.connect(lambda: self._select_file(self.output_json_edit, "JSON (*.json)"))
        ktx2_browse = QPushButton("Browse")
        ktx2_browse.clicked.connect(lambda: self._select_file(self.output_ktx2_edit, "KTX2 (*.ktx2)"))

        form = QFormLayout()
        form.addRow("Sheet PNG", self._row(self.output_image_edit, image_browse))
        form.addRow("PixiJS JSON", self._row(self.output_json_edit, json_browse))
        form.addRow("", self.ktx2_checkbox)
        form.addRow("KTX2", self._row(self.output_ktx2_edit, ktx2_browse))

        buttons = QHBoxLayout()
        cancel_button = QPushButton("Cancel")
        ok_button = QPushButton("Export")
        cancel_button.clicked.connect(self.reject)
        ok_button.clicked.connect(self.accept)
        buttons.addStretch(1)
        buttons.addWidget(cancel_button)
        buttons.addWidget(ok_button)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addLayout(buttons)

    def _row(self, edit: QLineEdit, button: QPushButton) -> QWidget:
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(edit)
        layout.addWidget(button)
        return widget

    def _select_file(self, target: QLineEdit, file_filter: str) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Save As", target.text().strip(), file_filter)
        if path:
            target.setText(path)

    def values(self) -> tuple[str, str, bool, str]:
        return (
            self.output_image_edit.text().strip(),
            self.output_json_edit.text().strip(),
            self.ktx2_checkbox.isChecked(),
            self.output_ktx2_edit.text().strip(),
        )


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
        self.output_image_edit = FileDropLineEdit("file")
        self.output_json_edit = FileDropLineEdit("file")
        self.output_ktx2_edit = FileDropLineEdit("file")
        self.name_pattern_edit = QLineEdit("{stem}.png")
        self.packing_combo = QComboBox()
        self.packing_combo.addItems(PACKING_ALGORITHMS)
        self.columns_spin = self._spin(0, 9999, 0)
        self.scale_spin = self._spin(1, 800, 100)
        self.downscale_spin = self._spin(1, 100, 100)
        self.shape_padding_spin = self._spin(0, 4096, 0)
        self.border_padding_spin = self._spin(0, 4096, 0)
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
        self.setCentralWidget(self.preview)

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

        toolbar = QToolBar("Tab Actions", self)
        toolbar.setMovable(True)
        self.addToolBar(Qt.TopToolBarArea, toolbar)
        export_button = self._toolbar_button("Export...")
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
            QDockWidget { color: #eef3f8; font-weight: 700; }
            QDockWidget::title {
                background: #19232d;
                padding: 8px 10px;
                text-align: left;
                border-bottom: 1px solid #32414b;
            }
            QToolBar {
                background: #19232d;
                border-bottom: 1px solid #32414b;
                spacing: 8px;
                padding: 8px;
            }
            QToolButton#toolbarButton {
                background: #253340;
                color: #eef3f8;
                border: 1px solid #32414b;
                border-radius: 6px;
                padding: 5px 9px;
                font-weight: 600;
            }
            QToolButton#toolbarButton:hover { background: #32414b; }
            QGroupBox {
                border: 1px solid #32414b;
                border-radius: 10px;
                margin-top: 10px;
                padding-top: 12px;
                background: #19232d;
                color: #edf3f8;
                font-weight: 600;
            }
            QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 4px; }
            QPushButton {
                background: #253340;
                color: #eef3f8;
                border: 1px solid #32414b;
                border-radius: 8px;
                padding: 8px 12px;
                font-weight: 600;
            }
            QPushButton:hover { background: #32414b; }
            QPushButton#helpButton {
                min-width: 22px;
                max-width: 22px;
                min-height: 22px;
                max-height: 22px;
                padding: 0;
                border-radius: 11px;
                font-weight: 700;
            }
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
        files_layout.addWidget(QLabel("Tip: drag files or spacers in the list, or drag sprites on the atlas preview."))
        layout.addWidget(files_box)
        return widget

    def _build_settings_panel(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)

        atlas_box = QGroupBox("Atlas")
        atlas_form = QFormLayout(atlas_box)
        atlas_form.addRow(build_help_label("Packing", "Grid keeps source order cells. Basic uses shelf packing. MaxRects packs rectangles tightly. Polygon adds alpha-outline metadata with MaxRects-style placement."), build_help_field(self.packing_combo, "Grid keeps source order cells. Basic uses shelf packing. MaxRects packs rectangles tightly. Polygon adds alpha-outline metadata with MaxRects-style placement."))
        atlas_form.addRow(build_help_label("Columns (0 = auto)", "Use 0 to let the tool choose a near-square grid automatically."), build_help_field(self.columns_spin, "Use 0 to let the tool choose a near-square grid automatically."))
        atlas_form.addRow(build_help_label("Prescale %", "Scales source sprites before trimming and packing."), build_help_field(self.scale_spin, "Scales source sprites before trimming and packing."))
        atlas_form.addRow(build_help_label("Export Downscale %", "Scales the final atlas and JSON frame coordinates during export."), build_help_field(self.downscale_spin, "Scales the final atlas and JSON frame coordinates during export."))
        atlas_form.addRow(build_help_label("Shape Padding", "Adds spacing between sprite cells."), build_help_field(self.shape_padding_spin, "Adds spacing between sprite cells."))
        atlas_form.addRow(build_help_label("Border Padding", "Adds spacing around the outer sheet border."), build_help_field(self.border_padding_spin, "Adds spacing around the outer sheet border."))
        atlas_form.addRow(build_help_label("Sprite Name Pattern", "Controls frame names. Variables: {stem}, {index}, {name}, {ext}"), build_help_field(self.name_pattern_edit, "Controls frame names. Variables: {stem}, {index}, {name}, {ext}"))
        atlas_form.addRow(build_help_label("JSON Format", "Choose PixiJS frame layout: hash or array."), build_help_field(self.data_format_combo, "Choose PixiJS frame layout: hash or array."))
        atlas_form.addRow("", self.trim_checkbox)
        atlas_form.addRow("", self.pot_checkbox)

        optimize_box = QGroupBox("PNG Optimization")
        optimize_form = QFormLayout(optimize_box)
        optimize_form.addRow(build_help_label("Pixel Format", "Simulates the selected pixel format in the exported PNG."), build_help_field(self.pixel_format_combo, "Simulates the selected pixel format in the exported PNG."))
        optimize_form.addRow("", self.pngquant_checkbox)
        optimize_form.addRow(build_help_label("pngquant Path", "Path to the pngquant executable."), build_browse_field(self.pngquant_path_edit, "Browse", lambda: self._select_tool_path(self.pngquant_path_edit), "Path to the pngquant executable."))
        optimize_form.addRow(build_help_label("pngquant Quality Min", "Minimum pngquant quality."), build_help_field(self.pngquant_quality_min_spin, "Minimum pngquant quality."))
        optimize_form.addRow(build_help_label("pngquant Quality Max", "Maximum pngquant quality."), build_help_field(self.pngquant_quality_max_spin, "Maximum pngquant quality."))
        optimize_form.addRow(build_help_label("pngquant Speed", "pngquant speed from 1 slowest to 11 fastest."), build_help_field(self.pngquant_speed_spin, "pngquant speed from 1 slowest to 11 fastest."))
        optimize_form.addRow("", self.zopfli_checkbox)
        optimize_form.addRow(build_help_label("Zopfli Path", "Path to the zopflipng executable."), build_browse_field(self.zopfli_path_edit, "Browse", lambda: self._select_tool_path(self.zopfli_path_edit), "Path to the zopflipng executable."))
        optimize_form.addRow(build_help_label("Zopfli Iterations", "More iterations can improve compression but slow export."), build_help_field(self.zopfli_iterations_spin, "More iterations can improve compression but slow export."))

        ktx_box = QGroupBox("KTX2")
        ktx_form = QFormLayout(ktx_box)
        ktx_form.addRow("", self.export_ktx2_checkbox)
        ktx_form.addRow(build_help_label("KTX2 Mode", "UASTC favors visual quality and faster transcoding. ETC1S favors smaller files."), build_help_field(self.ktx2_mode_combo, "UASTC favors visual quality and faster transcoding. ETC1S favors smaller files."))
        ktx_form.addRow("", self.generate_mipmaps_checkbox)
        ktx_form.addRow(build_help_label("Mipmap Filter", "Filter used while generating mipmap levels."), build_help_field(self.mipmap_filter_combo, "Filter used while generating mipmap levels."))
        ktx_form.addRow("", self.use_ktx2_in_json_checkbox)
        ktx_form.addRow(build_help_label("ktx Path", "Path to the modern KTX-Software `ktx` tool."), build_help_field(self.ktx_path_edit, "Path to the modern KTX-Software `ktx` tool. Click the help button to open official KTX releases."))

        paths_box = QGroupBox("Default Output Paths")
        paths_form = QFormLayout(paths_box)
        paths_form.addRow(
            build_help_label("Sheet PNG", "Default output path used by the export dialog."),
            build_browse_field(self.output_image_edit, "Browse", lambda: self._select_save_path(self.output_image_edit, "PNG (*.png)"), "Default output path used by the export dialog."),
        )
        paths_form.addRow(
            build_help_label("PixiJS JSON", "Default JSON path used by the export dialog."),
            build_browse_field(self.output_json_edit, "Browse", lambda: self._select_save_path(self.output_json_edit, "JSON (*.json)"), "Default JSON path used by the export dialog."),
        )
        paths_form.addRow(
            build_help_label("KTX2", "Default KTX2 path used by the export dialog."),
            build_browse_field(self.output_ktx2_edit, "Browse", lambda: self._select_save_path(self.output_ktx2_edit, "KTX2 (*.ktx2)"), "Default KTX2 path used by the export dialog."),
        )

        layout.addWidget(atlas_box)
        layout.addWidget(optimize_box)
        layout.addWidget(ktx_box)
        layout.addWidget(paths_box)
        layout.addStretch(1)
        return widget

    def _connect_signals(self) -> None:
        self.packing_combo.currentTextChanged.connect(self.update_preview)
        self.columns_spin.valueChanged.connect(self.update_preview)
        self.scale_spin.valueChanged.connect(self.update_preview)
        self.shape_padding_spin.valueChanged.connect(self.update_preview)
        self.border_padding_spin.valueChanged.connect(self.update_preview)
        self.name_pattern_edit.textChanged.connect(self.update_preview)
        self.trim_checkbox.toggled.connect(self.update_preview)
        self.pot_checkbox.toggled.connect(self.update_preview)
        self.pixel_format_combo.currentTextChanged.connect(self.update_preview)
        self.export_ktx2_checkbox.toggled.connect(self._sync_ktx2_ui)
        self.generate_mipmaps_checkbox.toggled.connect(self._sync_ktx2_ui)
        self.use_ktx2_in_json_checkbox.toggled.connect(self._sync_ktx2_ui)
        self.files_list.model().rowsMoved.connect(lambda *_: self.update_preview())
        self._sync_ktx2_ui()
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
                item = QListWidgetItem(file_path)
                item.setData(ITEM_KIND_ROLE, "file")
                item.setData(ITEM_PATH_ROLE, file_path)
                self.files_list.addItem(item)
        if files and not self.output_image_edit.text():
            base_dir = os.path.dirname(files[0])
            self.output_image_edit.setText(os.path.join(base_dir, "sheet.png"))
            self.output_json_edit.setText(os.path.join(base_dir, "sheet.json"))
            self.output_ktx2_edit.setText(os.path.join(base_dir, "sheet.ktx2"))
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
        enabled = self.export_ktx2_checkbox.isChecked()
        self.ktx2_mode_combo.setEnabled(enabled)
        self.generate_mipmaps_checkbox.setEnabled(enabled)
        self.mipmap_filter_combo.setEnabled(enabled and self.generate_mipmaps_checkbox.isChecked())
        self.ktx_path_edit.setEnabled(enabled)
        self.use_ktx2_in_json_checkbox.setEnabled(enabled)
        if not enabled:
            self.generate_mipmaps_checkbox.setChecked(False)
            self.use_ktx2_in_json_checkbox.setChecked(False)

    def log_message(self, message: str) -> None:
        self.log.append(message)

    def update_preview(self) -> None:
        try:
            file_paths = self._collect_file_paths()
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
            self.preview.set_preview(
                None,
                [],
                f"Preview error: {exc}",
                [("Status", "Error"), ("Reason", str(exc)), ("Sprites", "-"), ("Grid", "-")],
                region_labels=[],
            )

    def open_export_dialog(self) -> None:
        dialog = GenerateExportDialog(
            self,
            self.output_image_edit.text().strip(),
            self.output_json_edit.text().strip(),
            self.output_ktx2_edit.text().strip(),
            self.export_ktx2_checkbox.isChecked(),
        )
        if dialog.exec_() != QDialog.Accepted:
            return
        output_image, output_json, export_ktx2, output_ktx2 = dialog.values()
        self.export_sheet(output_image, output_json, export_ktx2, output_ktx2)

    def export_sheet(self, output_image: str, output_json: str, export_ktx2: bool, output_ktx2: str) -> None:
        self.log.clear()
        try:
            if not output_image or not output_json:
                raise ValueError("You must set both the sheet PNG path and JSON path.")
            if export_ktx2 and not output_ktx2:
                raise ValueError("KTX2 output path is required when KTX2 export is enabled.")

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

            self.output_image_edit.setText(output_image)
            self.output_json_edit.setText(output_json)
            self.output_ktx2_edit.setText(output_ktx2)

            self.log_message(f"Sheet created: {output_image}")
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
        except Exception as exc:
            QMessageBox.critical(self, "Error", str(exc))
            self.log_message(f"Error: {exc}")

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
