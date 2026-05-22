import json
import os

from PIL import Image
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (
    QCheckBox,
    QComboBox,
    QColorDialog,
    QDialog,
    QDockWidget,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QTextEdit,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from sprite_tool.constants import IMAGE_FILTER, JSON_FORMATS, KTX2_MODES, MIPMAP_FILTERS
from sprite_tool.models import FrameInfo, Ktx2Settings
from sprite_tool.services.images import (
    apply_transparent_color,
    build_grid_rects,
    build_output_name,
    detect_sprite_regions,
    fit_columns_rows,
    fit_sprite_size,
)
from sprite_tool.services.ktx2 import find_default_ktx_binary, run_ktx2_export
from sprite_tool.services.pixi import build_pixi_data
from sprite_tool.ui.widgets import FileDropLineEdit, PreviewPanel, build_browse_field, build_help_field, build_help_label


class SplitExportDialog(QDialog):
    def __init__(self, parent: QWidget, output_dir: str, json_path: str, ktx2_path: str, ktx2_enabled: bool) -> None:
        super().__init__(parent)
        self.setWindowTitle("Sprite Export")
        self.output_dir_edit = QLineEdit(output_dir)
        self.json_path_edit = QLineEdit(json_path)
        self.ktx2_checkbox = QCheckBox("Export KTX2")
        self.ktx2_checkbox.setChecked(ktx2_enabled)
        self.ktx2_path_edit = QLineEdit(ktx2_path)
        self.export_all_radio = QRadioButton("Export all regions")
        self.export_selected_radio = QRadioButton("Export selected regions only")
        self.export_all_radio.setChecked(True)
        self.transparent_checkbox = QCheckBox("Treat this color as transparent")
        self.color_edit = QLineEdit("#000000")
        self.tolerance_spin = QSpinBox()
        self.tolerance_spin.setRange(0, 255)
        self.tolerance_spin.setValue(8)

        dir_browse = QPushButton("Browse")
        dir_browse.clicked.connect(self.select_dir)
        json_browse = QPushButton("Browse")
        json_browse.clicked.connect(lambda: self._select_file(self.json_path_edit, "JSON (*.json)"))
        ktx2_browse = QPushButton("Browse")
        ktx2_browse.clicked.connect(lambda: self._select_file(self.ktx2_path_edit, "KTX2 (*.ktx2)"))
        color_pick = QPushButton("Pick")
        color_pick.clicked.connect(self.select_color)
        ok_button = QPushButton("Export")
        cancel_button = QPushButton("Cancel")
        ok_button.clicked.connect(self.accept)
        cancel_button.clicked.connect(self.reject)

        form = QFormLayout()
        form.addRow("Output Folder", self._row(self.output_dir_edit, dir_browse))
        form.addRow("PixiJS JSON", self._row(self.json_path_edit, json_browse))
        form.addRow("", self.export_all_radio)
        form.addRow("", self.export_selected_radio)
        form.addRow("", self.ktx2_checkbox)
        form.addRow("KTX2", self._row(self.ktx2_path_edit, ktx2_browse))
        form.addRow("", self.transparent_checkbox)
        form.addRow("Background Color", self._row(self.color_edit, color_pick))
        form.addRow("Tolerance", self.tolerance_spin)

        buttons = QHBoxLayout()
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

    def select_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Select Output Folder")
        if path:
            self.output_dir_edit.setText(path)

    def select_color(self) -> None:
        color = QColorDialog.getColor(QColor(self.color_edit.text()), self, "Pick Background Color")
        if color.isValid():
            self.color_edit.setText(color.name().upper())

    def values(self) -> tuple[str, str, bool, str, bool, bool, str, int]:
        return (
            self.output_dir_edit.text().strip(),
            self.json_path_edit.text().strip(),
            self.ktx2_checkbox.isChecked(),
            self.ktx2_path_edit.text().strip(),
            self.export_selected_radio.isChecked(),
            self.transparent_checkbox.isChecked(),
            self.color_edit.text().strip(),
            self.tolerance_spin.value(),
        )


class SplitSheetTab(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.sheet_path_edit = FileDropLineEdit("file", self.handle_sheet_drop)
        self.output_dir_edit = FileDropLineEdit("dir")
        self.prefix_edit = QLineEdit("sprite")
        self.name_pattern_edit = QLineEdit("{prefix}_{index:03d}.png")
        self.json_path_edit = FileDropLineEdit("file")
        self.output_ktx2_edit = FileDropLineEdit("file")
        self.detect_mode_combo = QComboBox()
        self.detect_mode_combo.addItems(["Grid", "Auto Detect Alpha"])
        self.columns_spin = self._spin(1, 9999, 1)
        self.rows_spin = self._spin(1, 9999, 1)
        self.sprite_width_spin = self._spin(1, 4096, 64)
        self.sprite_height_spin = self._spin(1, 4096, 64)
        self.shape_padding_spin = self._spin(0, 4096, 0)
        self.border_padding_spin = self._spin(0, 4096, 0)
        self.auto_fit_checkbox = QCheckBox("Auto-fit values to sheet size")
        self.auto_fit_checkbox.setChecked(True)
        self.detect_alpha_spin = self._spin(0, 255, 1)
        self.detect_min_area_spin = self._spin(1, 999999, 16)
        self.detect_padding_spin = self._spin(0, 512, 1)
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
        self.preview = PreviewPanel("Sprite Inspector")
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

        source_dock = QDockWidget("Source", self)
        source_dock.setFeatures(QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetFloatable)
        source_dock.setWidget(self._build_source_panel())
        self.addDockWidget(Qt.LeftDockWidgetArea, source_dock)

        settings_dock = QDockWidget("Cut Settings", self)
        settings_dock.setFeatures(QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetFloatable)
        settings_dock.setWidget(self._build_settings_panel())
        self.addDockWidget(Qt.LeftDockWidgetArea, settings_dock)
        self.splitDockWidget(source_dock, settings_dock, Qt.Vertical)

        console_dock = QDockWidget("Console", self)
        console_dock.setFeatures(QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetFloatable)
        console_dock.setWidget(self.log)
        self.addDockWidget(Qt.BottomDockWidgetArea, console_dock)
        console_dock.hide()

        source_dock.setMinimumWidth(340)
        settings_dock.setMinimumWidth(340)
        console_dock.setMinimumHeight(140)

        toolbar = QToolBar("Tab Actions", self)
        toolbar.setMovable(True)
        self.addToolBar(Qt.TopToolBarArea, toolbar)
        export_button = QPushButton("Export...")
        export_button.clicked.connect(self.open_export_dialog)
        refresh_button = QPushButton("Refresh Preview")
        refresh_button.clicked.connect(self.update_preview)
        toggle_console = QPushButton("Console")
        toggle_console.clicked.connect(lambda: console_dock.setVisible(not console_dock.isVisible()))
        toolbar.addWidget(export_button)
        toolbar.addWidget(refresh_button)
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

    def _build_source_panel(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        source_box = QGroupBox("Source Sheet")
        source_layout = QFormLayout(source_box)
        source_layout.addRow(
            build_help_label("Sheet PNG", "Select the source sheet image to inspect and cut."),
            build_browse_field(self.sheet_path_edit, "Browse", self.select_sheet, "Select the source sheet image to inspect and cut."),
        )
        source_layout.addRow(
            build_help_label("Default Output Folder", "Default sprite folder used by the export dialog."),
            build_browse_field(self.output_dir_edit, "Browse", self.select_output_dir, "Default sprite folder used by the export dialog."),
        )
        source_layout.addRow(
            build_help_label("File Prefix", "Base name used while exporting cut sprites."),
            build_help_field(self.prefix_edit, "Base name used while exporting cut sprites."),
        )
        source_layout.addRow(
            build_help_label("Cut Mode", "Grid uses rows and columns. Auto Detect Alpha looks for opaque islands."),
            build_help_field(self.detect_mode_combo, "Grid uses rows and columns. Auto Detect Alpha looks for opaque islands."),
        )
        source_layout.addRow(
            build_help_label("Sprite Name Pattern", "Variables: {prefix}, {index}, {stem}, {name}, {ext}"),
            build_help_field(self.name_pattern_edit, "Variables: {prefix}, {index}, {stem}, {name}, {ext}"),
        )
        source_layout.addRow(
            build_help_label("Default JSON", "Default JSON path used by the export dialog."),
            build_browse_field(self.json_path_edit, "Browse", lambda: self._select_save_path(self.json_path_edit, "JSON (*.json)"), "Default JSON path used by the export dialog."),
        )
        source_layout.addRow(
            build_help_label("Default KTX2", "Default KTX2 path used by the export dialog."),
            build_browse_field(self.output_ktx2_edit, "Browse", lambda: self._select_save_path(self.output_ktx2_edit, "KTX2 (*.ktx2)"), "Default KTX2 path used by the export dialog."),
        )
        source_layout.addRow(
            build_help_label("JSON Format", "Choose PixiJS frame layout: hash or array."),
            build_help_field(self.data_format_combo, "Choose PixiJS frame layout: hash or array."),
        )
        source_layout.addRow(QLabel("Pattern variables: {prefix}, {index}, {stem}, {name}, {ext}"))

        layout.addWidget(source_box)
        return widget

    def _build_settings_panel(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)

        grid_box = QGroupBox("Grid / Detect")
        grid_layout = QFormLayout(grid_box)
        self.auto_fit_checkbox.setToolTip("Keeps grid values in sync with the source sheet size.")
        grid_layout.addRow("", self.auto_fit_checkbox)
        grid_layout.addRow(build_help_label("Columns", "Horizontal slice count in grid mode."), build_help_field(self.columns_spin, "Horizontal slice count in grid mode."))
        grid_layout.addRow(build_help_label("Rows", "Vertical slice count in grid mode."), build_help_field(self.rows_spin, "Vertical slice count in grid mode."))
        grid_layout.addRow(build_help_label("Sprite Width", "Width of each grid cell."), build_help_field(self.sprite_width_spin, "Width of each grid cell."))
        grid_layout.addRow(build_help_label("Sprite Height", "Height of each grid cell."), build_help_field(self.sprite_height_spin, "Height of each grid cell."))
        grid_layout.addRow(build_help_label("Shape Padding", "Spacing between detected sprite cells."), build_help_field(self.shape_padding_spin, "Spacing between detected sprite cells."))
        grid_layout.addRow(build_help_label("Border Padding", "Padding around the outer sheet border."), build_help_field(self.border_padding_spin, "Padding around the outer sheet border."))
        grid_layout.addRow(build_help_label("Detect Alpha >", "Pixels above this alpha threshold are treated as solid."), build_help_field(self.detect_alpha_spin, "Pixels above this alpha threshold are treated as solid."))
        grid_layout.addRow(build_help_label("Detect Min Area", "Ignore tiny connected regions below this area."), build_help_field(self.detect_min_area_spin, "Ignore tiny connected regions below this area."))
        grid_layout.addRow(build_help_label("Detect Padding", "Extra padding added around each detected region."), build_help_field(self.detect_padding_spin, "Extra padding added around each detected region."))

        ktx_box = QGroupBox("KTX2")
        ktx_form = QFormLayout(ktx_box)
        ktx_form.addRow("", self.export_ktx2_checkbox)
        ktx_form.addRow(build_help_label("KTX2 Mode", "UASTC favors quality and faster transcoding. ETC1S favors smaller files."), build_help_field(self.ktx2_mode_combo, "UASTC favors quality and faster transcoding. ETC1S favors smaller files."))
        ktx_form.addRow("", self.generate_mipmaps_checkbox)
        ktx_form.addRow(build_help_label("Mipmap Filter", "Filter used while generating mipmap levels."), build_help_field(self.mipmap_filter_combo, "Filter used while generating mipmap levels."))
        ktx_form.addRow("", self.use_ktx2_in_json_checkbox)
        ktx_form.addRow(build_help_label("ktx Path", "Path to the modern KTX-Software `ktx` tool."), build_help_field(self.ktx_path_edit, "Path to the modern KTX-Software `ktx` tool. Click the help button to open official KTX releases."))

        layout.addWidget(grid_box)
        layout.addWidget(ktx_box)
        layout.addStretch(1)
        return widget

    def _connect_signals(self) -> None:
        self.columns_spin.valueChanged.connect(lambda _: self.handle_grid_change("grid"))
        self.rows_spin.valueChanged.connect(lambda _: self.handle_grid_change("grid"))
        self.sprite_width_spin.valueChanged.connect(lambda _: self.handle_grid_change("size"))
        self.sprite_height_spin.valueChanged.connect(lambda _: self.handle_grid_change("size"))
        self.shape_padding_spin.valueChanged.connect(lambda _: self.handle_grid_change("size"))
        self.border_padding_spin.valueChanged.connect(lambda _: self.handle_grid_change("size"))
        self.detect_alpha_spin.valueChanged.connect(lambda _: self.handle_grid_change("detect"))
        self.detect_min_area_spin.valueChanged.connect(lambda _: self.handle_grid_change("detect"))
        self.detect_padding_spin.valueChanged.connect(lambda _: self.handle_grid_change("detect"))
        self.detect_mode_combo.currentTextChanged.connect(lambda _: self.handle_grid_change("detect"))
        self.sheet_path_edit.textChanged.connect(lambda _: self.handle_grid_change("sheet"))
        self.auto_fit_checkbox.toggled.connect(lambda _: self.handle_grid_change("sheet"))
        self.export_ktx2_checkbox.toggled.connect(self._sync_ktx2_ui)
        self.generate_mipmaps_checkbox.toggled.connect(self._sync_ktx2_ui)
        self.use_ktx2_in_json_checkbox.toggled.connect(self._sync_ktx2_ui)
        self._sync_ktx2_ui()
        self.update_preview()

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
        is_grid = self.detect_mode_combo.currentText() == "Grid"
        for widget in (
            self.auto_fit_checkbox,
            self.columns_spin,
            self.rows_spin,
            self.sprite_width_spin,
            self.sprite_height_spin,
            self.shape_padding_spin,
            self.border_padding_spin,
        ):
            widget.setEnabled(is_grid)
        for widget in (self.detect_alpha_spin, self.detect_min_area_spin, self.detect_padding_spin):
            widget.setEnabled(not is_grid)

    def log_message(self, message: str) -> None:
        self.log.append(message)

    def handle_sheet_drop(self) -> None:
        path = self.sheet_path_edit.text().strip()
        if path:
            self._populate_default_paths(path)
            self.update_preview()

    def _populate_default_paths(self, path: str) -> None:
        base_dir = os.path.dirname(path)
        base_name = os.path.splitext(os.path.basename(path))[0]
        if not self.output_dir_edit.text():
            self.output_dir_edit.setText(os.path.join(base_dir, f"{base_name}_sprites"))
        if not self.json_path_edit.text():
            self.json_path_edit.setText(os.path.join(base_dir, f"{base_name}.json"))
        if not self.output_ktx2_edit.text():
            self.output_ktx2_edit.setText(os.path.join(base_dir, f"{base_name}.ktx2"))

    def _select_save_path(self, target: QLineEdit, file_filter: str) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Select Output Path", target.text().strip(), file_filter)
        if path:
            target.setText(path)

    def select_sheet(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Select Sheet PNG", "", IMAGE_FILTER)
        if path:
            self.sheet_path_edit.setText(path)
            self._populate_default_paths(path)
            self.update_preview()

    def select_output_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Select Output Folder")
        if path:
            self.output_dir_edit.setText(path)

    def _load_sheet(self) -> Image.Image:
        sheet_path = self.sheet_path_edit.text().strip()
        if not sheet_path:
            raise ValueError("You must select a source sheet.")
        with Image.open(sheet_path) as opened:
            return opened.convert("RGBA")

    def _sheet_size(self):
        path = self.sheet_path_edit.text().strip()
        if not path:
            return None
        try:
            with Image.open(path) as opened:
                return opened.size
        except Exception:
            return None

    def handle_grid_change(self, source: str) -> None:
        self.sync_grid_controls(source)
        self._sync_ktx2_ui()
        self.update_preview()

    def sync_grid_controls(self, source: str) -> None:
        if self.detect_mode_combo.currentText() != "Grid" or not self.auto_fit_checkbox.isChecked():
            return
        sheet_size = self._sheet_size()
        if not sheet_size:
            return
        sheet_w, sheet_h = sheet_size
        shape_padding = self.shape_padding_spin.value()
        border_padding = self.border_padding_spin.value()

        widgets = [self.columns_spin, self.rows_spin, self.sprite_width_spin, self.sprite_height_spin]
        for widget in widgets:
            widget.blockSignals(True)
        try:
            if source in {"size", "sheet"}:
                columns, rows = fit_columns_rows(
                    sheet_w, sheet_h,
                    self.sprite_width_spin.value(), self.sprite_height_spin.value(),
                    shape_padding, border_padding,
                )
                self.columns_spin.setValue(columns)
                self.rows_spin.setValue(rows)
            else:
                sprite_w, sprite_h = fit_sprite_size(
                    sheet_w, sheet_h,
                    self.columns_spin.value(), self.rows_spin.value(),
                    shape_padding, border_padding,
                )
                self.sprite_width_spin.setValue(sprite_w)
                self.sprite_height_spin.setValue(sprite_h)
        finally:
            for widget in widgets:
                widget.blockSignals(False)

    def _preview_rects(self):
        sheet = self._load_sheet()
        if self.detect_mode_combo.currentText() == "Auto Detect Alpha":
            rects = detect_sprite_regions(
                sheet=sheet,
                alpha_threshold=self.detect_alpha_spin.value(),
                min_area=self.detect_min_area_spin.value(),
                padding=self.detect_padding_spin.value(),
            )
            return sheet, rects
        rects = build_grid_rects(
            sheet=sheet,
            columns=self.columns_spin.value(),
            rows=self.rows_spin.value(),
            sprite_w=self.sprite_width_spin.value(),
            sprite_h=self.sprite_height_spin.value(),
            shape_padding=self.shape_padding_spin.value(),
            border_padding=self.border_padding_spin.value(),
        )
        return sheet, rects

    def update_preview(self) -> None:
        try:
            if not self.sheet_path_edit.text().strip():
                self.preview.set_preview(None, [], "Waiting for a source sheet.", region_labels=[])
                return
            sheet, rects = self._preview_rects()
            covered_area = sum(w * h for _index, _x, _y, w, h in rects)
            coverage = (covered_area / max(1, sheet.width * sheet.height)) * 100.0
            mode = self.detect_mode_combo.currentText()
            labels = [f"Region {index:03d}" for index, _x, _y, _w, _h in rects]
            self.preview.set_preview(
                sheet,
                [(x, y, w, h) for _index, x, y, w, h in rects],
                f"{sheet.width}x{sheet.height} px | {mode} | {len(rects)} sprite",
                [
                    ("Sheet", f"{sheet.width} x {sheet.height}"),
                    ("Mode", mode),
                    ("Coverage", f"%{coverage:.1f}"),
                    ("Count", str(len(rects))),
                ],
                region_labels=labels,
            )
        except Exception as exc:
            self.preview.set_preview(
                None, [], f"Preview error: {exc}",
                [("Status", "Error"), ("Reason", str(exc)), ("Sheet", "-"), ("Mode", "-")],
                region_labels=[],
            )

    def open_export_dialog(self) -> None:
        dialog = SplitExportDialog(
            self,
            self.output_dir_edit.text().strip(),
            self.json_path_edit.text().strip(),
            self.output_ktx2_edit.text().strip(),
            self.export_ktx2_checkbox.isChecked(),
        )
        if dialog.exec_() != QDialog.Accepted:
            return
        (
            output_dir,
            json_path,
            export_ktx2,
            output_ktx2,
            selected_only,
            transparent_enabled,
            color_text,
            tolerance,
        ) = dialog.values()
        self.export_sprites(
            output_dir,
            json_path,
            export_ktx2,
            output_ktx2,
            selected_only,
            transparent_enabled,
            color_text,
            tolerance,
        )

    def export_sprites(
        self,
        output_dir: str,
        json_path: str,
        export_ktx2: bool,
        output_ktx2: str,
        selected_only: bool,
        transparent_enabled: bool,
        color_text: str,
        tolerance: int,
    ) -> None:
        self.log.clear()
        try:
            if not output_dir or not json_path:
                raise ValueError("You must set both the output folder and JSON path.")
            if export_ktx2 and not output_ktx2:
                raise ValueError("KTX2 output path is required when KTX2 export is enabled.")

            sheet, rects = self._preview_rects()
            selected_indexes = self.preview.get_selected_indexes()
            if selected_only and not selected_indexes:
                raise ValueError("Select one or more regions from the inspector list for selected export.")

            active_rects = rects if not selected_only else [item for item in rects if item[0] in selected_indexes]
            prefix = self.prefix_edit.text().strip() or "sprite"
            pattern = self.name_pattern_edit.text().strip() or "{prefix}_{index:03d}.png"

            transparent_rgb = None
            if transparent_enabled:
                color = QColor(color_text)
                if not color.isValid():
                    raise ValueError("Enter a valid color value such as #000000.")
                transparent_rgb = (color.red(), color.green(), color.blue())

            os.makedirs(output_dir, exist_ok=True)
            os.makedirs(os.path.dirname(json_path) or ".", exist_ok=True)

            frames = []
            source_path = self.sheet_path_edit.text().strip()
            for index, x, y, w, h in active_rects:
                sprite = sheet.crop((x, y, x + w, y + h))
                if transparent_rgb is not None:
                    sprite = apply_transparent_color(sprite, transparent_rgb, tolerance)
                file_name = build_output_name(pattern, source_path, index, prefix=prefix)
                sprite.save(os.path.join(output_dir, file_name))
                frames.append(FrameInfo(name=file_name, x=x, y=y, w=w, h=h, source_w=w, source_h=h))

            ktx2 = self._build_ktx2_settings(export_ktx2, output_ktx2)
            if ktx2.enabled:
                command_text = run_ktx2_export(
                    self.sheet_path_edit.text().strip(),
                    ktx2.output_path,
                    ktx2.encoder_mode,
                    ktx2.ktx_path,
                    ktx2.generate_mipmaps,
                    ktx2.mipmap_filter,
                )
                self.log_message(f"KTX2 created: {ktx2.output_path}")
                self.log_message(f"ktx: {command_text}")

            json_image_name = os.path.basename(ktx2.output_path) if ktx2.use_in_json else os.path.basename(self.sheet_path_edit.text().strip())
            pixi_data = build_pixi_data(
                json_image_name,
                sheet.width,
                sheet.height,
                frames,
                self.data_format_combo.currentText(),
            )
            with open(json_path, "w", encoding="utf-8") as handle:
                json.dump(pixi_data, handle, indent=2)

            self.output_dir_edit.setText(output_dir)
            self.json_path_edit.setText(json_path)
            self.output_ktx2_edit.setText(output_ktx2)

            self.log_message(f"Sprites saved: {output_dir}")
            self.log_message(f"PixiJS JSON created: {json_path}")
            self.log_message(f"Cut mode: {self.detect_mode_combo.currentText()}")
            self.log_message(f"Sprite pattern: {pattern}")
            self.log_message(f"Export count: {len(frames)}")
            self.log_message(f"Scope: {'selected' if selected_only else 'all'}")
            if transparent_rgb is not None:
                self.log_message(f"Transparent color: {color_text} tolerance {tolerance}")
            self.log_message(f"KTX2: {'on' if ktx2.enabled else 'off'}")
            self.log_message(f"Mipmap: {'on' if ktx2.generate_mipmaps else 'off'}")
        except Exception as exc:
            QMessageBox.critical(self, "Error", str(exc))
            self.log_message(f"Error: {exc}")
