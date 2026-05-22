import os
from typing import Callable, List, Optional, Sequence, Tuple

from PIL import Image
from PyQt5.QtCore import QPoint, QRect, QRectF, Qt, QUrl
from PyQt5.QtGui import QColor, QDesktopServices, QImage, QPainter, QPen, QPixmap
from PyQt5.QtWidgets import (
    QFrame,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QGraphicsView,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from sprite_tool.constants import KTX_RELEASES_URL
from sprite_tool.services.images import is_image_file


def pil_to_qpixmap(image: Image.Image) -> QPixmap:
    rgba = image.convert("RGBA")
    data = rgba.tobytes("raw", "RGBA")
    qimage = QImage(data, rgba.width, rgba.height, rgba.width * 4, QImage.Format_RGBA8888).copy()
    return QPixmap.fromImage(qimage)


def build_help_label(text: str, help_text: str) -> QLabel:
    label = QLabel(text)
    label.setToolTip(help_text)
    return label


def _show_help_dialog(parent: QWidget, help_text: str) -> None:
    dialog = QMessageBox(parent)
    dialog.setWindowTitle("Field Help")
    dialog.setIcon(QMessageBox.Information)
    dialog.setText(help_text)
    open_release_button = None
    if "ktx" in help_text.lower():
        open_release_button = dialog.addButton("Open KTX Releases", QMessageBox.ActionRole)
    dialog.addButton(QMessageBox.Ok)
    dialog.exec_()
    if dialog.clickedButton() is open_release_button:
        QDesktopServices.openUrl(QUrl(KTX_RELEASES_URL))


def build_help_field(field: QWidget, help_text: str) -> QWidget:
    wrapper = QWidget()
    wrapper.setObjectName("helpField")
    layout = QHBoxLayout(wrapper)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(6)
    button = QPushButton("?")
    button.setObjectName("helpButton")
    button.setFixedSize(22, 22)
    button.setToolTip(help_text)
    button.clicked.connect(lambda: _show_help_dialog(wrapper, help_text))
    layout.addWidget(field, 1)
    layout.addWidget(button)
    return wrapper


def build_browse_field(
    field: QWidget,
    browse_text: str,
    on_browse: Callable[[], None],
    help_text: str = "",
) -> QWidget:
    wrapper = QWidget()
    layout = QHBoxLayout(wrapper)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(8)
    browse_button = QPushButton(browse_text)
    browse_button.setObjectName("browseButton")
    browse_button.setFixedWidth(90)
    browse_button.clicked.connect(on_browse)
    layout.addWidget(field, 1)
    layout.addWidget(browse_button)
    if help_text:
        help_button = QPushButton("?")
        help_button.setObjectName("helpButton")
        help_button.setFixedSize(22, 22)
        help_button.setToolTip(help_text)
        help_button.clicked.connect(lambda: _show_help_dialog(wrapper, help_text))
        layout.addWidget(help_button)
    return wrapper


class FileDropLineEdit(QLineEdit):
    def __init__(self, mode: str, on_drop: Optional[Callable[[], None]] = None) -> None:
        super().__init__()
        self.mode = mode
        self.on_drop = on_drop
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event) -> None:  # type: ignore[override]
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event) -> None:  # type: ignore[override]
        urls = event.mimeData().urls()
        if not urls:
            return
        path = urls[0].toLocalFile()
        if self.mode == "file" and os.path.isfile(path):
            self.setText(path)
        elif self.mode == "dir":
            self.setText(path if os.path.isdir(path) else os.path.dirname(path))
        if self.on_drop:
            self.on_drop()
        event.acceptProposedAction()


class SpriteListWidget(QListWidget):
    def __init__(self, on_files_dropped: Callable[[List[str]], None]) -> None:
        super().__init__()
        self.on_files_dropped = on_files_dropped
        self.setAcceptDrops(True)
        self.setDragDropMode(QListWidget.InternalMove)

    def dragEnterEvent(self, event) -> None:  # type: ignore[override]
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:  # type: ignore[override]
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:  # type: ignore[override]
        if event.mimeData().hasUrls():
            files = [url.toLocalFile() for url in event.mimeData().urls()]
            image_files = [path for path in files if is_image_file(path)]
            if image_files:
                self.on_files_dropped(image_files)
            event.acceptProposedAction()
            return
        super().dropEvent(event)


class ZoomableGraphicsView(QGraphicsView):
    def __init__(self) -> None:
        super().__init__()
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self._pixmap_item = QGraphicsPixmapItem()
        self._scene.addItem(self._pixmap_item)
        self._zoom = 1.0
        self._drag_pos: Optional[QPoint] = None
        self._press_pos: Optional[QPoint] = None
        self._click_callback: Optional[Callable[[QPoint, object], None]] = None
        self._can_start_drag_callback: Optional[Callable[[float, float], bool]] = None
        self._drag_finish_callback: Optional[Callable[[float, float, float, float], None]] = None
        self._scene_drag_start: Optional[QPoint] = None
        self._is_item_drag = False
        self.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        self.setBackgroundBrush(QColor("#10151c"))
        self.setFrameShape(QFrame.NoFrame)
        self.setDragMode(QGraphicsView.NoDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorViewCenter)

    def set_pixmap(self, pixmap: QPixmap) -> None:
        self.set_pixmap_with_mode(pixmap, preserve_view=False)

    def set_pixmap_with_mode(self, pixmap: QPixmap, preserve_view: bool) -> None:
        horizontal_value = self.horizontalScrollBar().value()
        vertical_value = self.verticalScrollBar().value()
        transform = self.transform()
        zoom = self._zoom
        self._pixmap_item.setPixmap(pixmap)
        self._scene.setSceneRect(self._pixmap_item.boundingRect())
        if preserve_view and not pixmap.isNull():
            self.setTransform(transform)
            self._zoom = zoom
            self.horizontalScrollBar().setValue(horizontal_value)
            self.verticalScrollBar().setValue(vertical_value)
            return
        self.reset_view()

    def set_click_callback(self, callback: Optional[Callable[[QPoint, object], None]]) -> None:
        self._click_callback = callback

    def set_item_drag_callbacks(
        self,
        can_start: Optional[Callable[[float, float], bool]],
        finish: Optional[Callable[[float, float, float, float], None]],
    ) -> None:
        self._can_start_drag_callback = can_start
        self._drag_finish_callback = finish

    def clear_pixmap(self) -> None:
        self._pixmap_item.setPixmap(QPixmap())
        self._scene.setSceneRect(QRectF())
        self.resetTransform()
        self._zoom = 1.0

    def reset_view(self) -> None:
        self.resetTransform()
        self._zoom = 1.0
        if not self._pixmap_item.pixmap().isNull():
            self.fitInView(self._pixmap_item, Qt.KeepAspectRatio)

    def zoom_in(self) -> None:
        self._apply_zoom(1.2)

    def zoom_out(self) -> None:
        self._apply_zoom(1 / 1.2)

    def _apply_zoom(self, factor: float) -> None:
        if self._pixmap_item.pixmap().isNull():
            return
        next_zoom = self._zoom * factor
        if next_zoom < 0.05 or next_zoom > 40:
            return
        self.scale(factor, factor)
        self._zoom = next_zoom

    def wheelEvent(self, event) -> None:  # type: ignore[override]
        if event.angleDelta().y() > 0:
            self.zoom_in()
        else:
            self.zoom_out()

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.LeftButton:
            self._press_pos = event.pos()
            scene_pos = self.mapToScene(event.pos())
            self._scene_drag_start = QPoint(round(scene_pos.x()), round(scene_pos.y()))
            self._is_item_drag = (
                self._can_start_drag_callback is not None
                and self._can_start_drag_callback(scene_pos.x(), scene_pos.y())
            )
            if self._is_item_drag:
                self.setCursor(Qt.SizeAllCursor)
            else:
                self._drag_pos = event.pos()
                self.setCursor(Qt.ClosedHandCursor)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # type: ignore[override]
        if self._drag_pos is not None and not self._is_item_drag:
            delta = event.pos() - self._drag_pos
            self._drag_pos = event.pos()
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - delta.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - delta.y())
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.LeftButton:
            was_click = self._press_pos is not None and (event.pos() - self._press_pos).manhattanLength() <= 4
            was_item_drag = self._is_item_drag and not was_click
            start_pos = self._scene_drag_start
            self._drag_pos = None
            self._press_pos = None
            self._scene_drag_start = None
            self._is_item_drag = False
            self.setCursor(Qt.ArrowCursor)
            if was_item_drag and start_pos is not None and self._drag_finish_callback is not None:
                end_pos = self.mapToScene(event.pos())
                self._drag_finish_callback(start_pos.x(), start_pos.y(), end_pos.x(), end_pos.y())
            elif was_click and self._click_callback is not None:
                self._click_callback(event.pos(), event.modifiers())
        super().mouseReleaseEvent(event)


class PreviewWindow(QWidget):
    def __init__(self, title: str) -> None:
        super().__init__()
        self.setWindowTitle(title)
        self.resize(1200, 900)
        self.view = ZoomableGraphicsView()
        self.info_label = QLabel(" ")
        self.info_label.setWordWrap(True)

        controls = QHBoxLayout()
        zoom_in = QPushButton("+")
        zoom_out = QPushButton("-")
        reset = QPushButton("Fit")
        zoom_in.clicked.connect(self.view.zoom_in)
        zoom_out.clicked.connect(self.view.zoom_out)
        reset.clicked.connect(self.view.reset_view)
        controls.addWidget(zoom_in)
        controls.addWidget(zoom_out)
        controls.addWidget(reset)
        controls.addStretch(1)

        layout = QVBoxLayout(self)
        layout.addLayout(controls)
        layout.addWidget(self.view, 1)
        layout.addWidget(self.info_label)

    def set_pixmap(self, pixmap: QPixmap, description: str) -> None:
        self.view.set_pixmap(pixmap)
        self.info_label.setText(description)


class PreviewPanel(QWidget):
    def __init__(self, title: str) -> None:
        super().__init__()
        self.title = title
        self.title_label = QLabel(title)
        self.title_label.setObjectName("previewTitle")
        self.view = ZoomableGraphicsView()
        self.view.setObjectName("previewCanvas")
        self.info_grid = QGridLayout()
        self.info_cards: List[Tuple[QLabel, QLabel]] = []
        self.preview_window = PreviewWindow(title)
        self.base_pixmap = QPixmap()
        self._source_pixmap = QPixmap()
        self._rects: List[Tuple[int, int, int, int]] = []
        self._selected_indexes: List[int] = []
        self._last_selected_index: Optional[int] = None
        self._region_labels: List[str] = []
        self._reorder_callback: Optional[Callable[[int, int, bool], None]] = None
        self._selection_callback: Optional[Callable[[List[int]], None]] = None
        self.view.set_click_callback(self._handle_view_click)
        self.view.set_item_drag_callbacks(self._can_start_reorder_drag, self._finish_reorder_drag)

        controls = QHBoxLayout()
        zoom_out = QPushButton("-")
        zoom_in = QPushButton("+")
        fit_button = QPushButton("Fit")
        popout_button = QPushButton("Pop Out")
        zoom_out.clicked.connect(self.view.zoom_out)
        zoom_in.clicked.connect(self.view.zoom_in)
        fit_button.clicked.connect(self.view.reset_view)
        popout_button.clicked.connect(self.open_separate_window)
        controls.addWidget(zoom_out)
        controls.addWidget(zoom_in)
        controls.addWidget(fit_button)
        controls.addWidget(popout_button)
        controls.addStretch(1)

        for index in range(4):
            card = QFrame()
            card.setObjectName("infoCard")
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(10, 8, 10, 8)
            title_label = QLabel("-")
            title_label.setObjectName("infoCardTitle")
            value_label = QLabel("-")
            value_label.setObjectName("infoCardValue")
            value_label.setWordWrap(True)
            card_layout.addWidget(title_label)
            card_layout.addWidget(value_label)
            self.info_grid.addWidget(card, index // 2, index % 2)
            self.info_cards.append((title_label, value_label))

        self.info_label = QLabel(" ")
        self.info_label.setObjectName("previewInfo")
        self.info_label.setWordWrap(True)
        self.region_list = QListWidget()
        self.region_list.setSelectionMode(QListWidget.ExtendedSelection)
        self.region_list.setObjectName("previewRegionList")
        self.region_list.itemSelectionChanged.connect(self._handle_region_list_selection)
        self.inspector = QTextEdit()
        self.inspector.setReadOnly(True)
        self.inspector.setObjectName("previewInspector")
        self.inspector.setMinimumHeight(150)

        canvas_panel = QWidget()
        canvas_layout = QVBoxLayout(canvas_panel)
        canvas_layout.setContentsMargins(0, 0, 0, 0)
        canvas_layout.setSpacing(10)
        canvas_layout.addWidget(self.title_label)
        canvas_layout.addLayout(controls)
        canvas_layout.addWidget(self.view, 1)
        canvas_layout.addWidget(self.info_label)

        sidebar = QWidget()
        sidebar.setObjectName("previewSidebar")
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(0, 0, 0, 0)
        sidebar_layout.setSpacing(10)
        sidebar_layout.addLayout(self.info_grid)
        sidebar_layout.addWidget(QLabel("Region List"))
        sidebar_layout.addWidget(self.region_list, 1)
        sidebar_layout.addWidget(QLabel("Inspector"))
        sidebar_layout.addWidget(self.inspector, 1)

        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(canvas_panel)
        splitter.addWidget(sidebar)
        splitter.setStretchFactor(0, 5)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([960, 360])

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)
        layout.addWidget(splitter, 1)
        self.setStyleSheet(
            """
            QLabel#previewTitle { font-size: 16px; font-weight: 700; color: #eef3f8; }
            QGraphicsView#previewCanvas { background: #10151c; border: 1px solid #2d3947; border-radius: 10px; }
            QFrame#infoCard { background: #17212b; border: 1px solid #283646; border-radius: 8px; }
            QLabel#infoCardTitle { color: #8fa4b8; font-size: 11px; }
            QLabel#infoCardValue { color: #f3f7fb; font-size: 13px; font-weight: 600; }
            QLabel#previewInfo { color: #b8c7d6; font-size: 12px; }
            QWidget#previewSidebar {
                background: #0d141b;
                border: 1px solid #2d3947;
                border-radius: 10px;
                padding: 8px;
            }
            QListWidget#previewRegionList { background: #0d141b; color: #dce5ee; border: 1px solid #2d3947; border-radius: 8px; }
            QTextEdit#previewInspector { background: #0d141b; color: #dce5ee; border: 1px solid #2d3947; border-radius: 8px; font-family: monospace; }
            QSplitter::handle {
                background: #17212b;
                width: 6px;
            }
            """
        )

    def set_preview(
        self,
        image: Optional[Image.Image],
        rects: Sequence[Tuple[int, int, int, int]],
        description: str,
        info_cards: Optional[Sequence[Tuple[str, str]]] = None,
        region_labels: Optional[Sequence[str]] = None,
    ) -> None:
        if image is None:
            self.base_pixmap = QPixmap()
            self._source_pixmap = QPixmap()
            self._rects = []
            self._selected_indexes = []
            self._last_selected_index = None
            self._region_labels = []
            self.view.clear_pixmap()
            self.info_label.setText(description)
            self._set_info_cards(info_cards or [])
            self.region_list.clear()
            self.inspector.setPlainText(f"Status: waiting\nMessage: {description}")
            self.preview_window.info_label.setText(description)
            return

        pixmap = pil_to_qpixmap(image)
        self._source_pixmap = pixmap
        self._rects = list(rects)
        self._region_labels = list(region_labels or [])
        self._selected_indexes = [index for index in self._selected_indexes if index < len(self._rects)]
        self._render_canvas()
        self.info_label.setText(description)
        self._set_info_cards(info_cards or [])
        self._set_region_list(rects, region_labels or [])
        self._sync_region_list_selection()
        self.inspector.setPlainText(self._build_inspector_text(image, rects, description, info_cards or []))
        if self.preview_window.isVisible():
            self.preview_window.set_pixmap(self.base_pixmap, description)

    def _render_canvas(self) -> None:
        if self._source_pixmap.isNull():
            self.base_pixmap = QPixmap()
            self.view.clear_pixmap()
            return
        canvas = self._source_pixmap.copy()
        painter = QPainter(canvas)
        fill_color = QColor(31, 184, 205, 55)
        line_color = QColor(63, 220, 255)
        selected_fill = QColor(255, 196, 0, 80)
        selected_line = QColor(255, 214, 64)
        base_width = max(1, min(canvas.width(), canvas.height()) // 220 + 1)
        for index, (x, y, w, h) in enumerate(self._rects):
            is_selected = index in self._selected_indexes
            painter.setPen(QPen(selected_line if is_selected else line_color, base_width + (1 if is_selected else 0)))
            painter.fillRect(QRect(x, y, w, h), selected_fill if is_selected else fill_color)
            painter.drawRect(QRect(x, y, w, h))
        painter.end()

        self.base_pixmap = canvas
        self.view.set_pixmap_with_mode(self.base_pixmap, preserve_view=True)
        if self.preview_window.isVisible():
            self.preview_window.set_pixmap(self.base_pixmap, self.info_label.text())

    def open_separate_window(self) -> None:
        self.preview_window.show()
        self.preview_window.raise_()
        self.preview_window.activateWindow()
        if not self.base_pixmap.isNull():
            self.preview_window.set_pixmap(self.base_pixmap, self.info_label.text())

    def _set_info_cards(self, info_cards: Sequence[Tuple[str, str]]) -> None:
        for index, (title_label, value_label) in enumerate(self.info_cards):
            if index < len(info_cards):
                title_label.setText(info_cards[index][0])
                value_label.setText(info_cards[index][1])
            else:
                title_label.setText("-")
                value_label.setText("-")

    def _set_region_list(
        self,
        rects: Sequence[Tuple[int, int, int, int]],
        region_labels: Sequence[str],
    ) -> None:
        self.region_list.clear()
        for index, (x, y, w, h) in enumerate(rects):
            label = region_labels[index] if index < len(region_labels) else f"Region {index:03d}"
            item = QListWidgetItem(f"{label} | x={x} y={y} w={w} h={h}")
            item.setData(Qt.UserRole, index)
            self.region_list.addItem(item)

    def get_selected_indexes(self) -> List[int]:
        return list(self._selected_indexes)

    def set_reorder_callback(self, callback: Optional[Callable[[int, int, bool], None]]) -> None:
        self._reorder_callback = callback

    def set_selection_callback(self, callback: Optional[Callable[[List[int]], None]]) -> None:
        self._selection_callback = callback

    def _can_start_reorder_drag(self, x: float, y: float) -> bool:
        return self._reorder_callback is not None and self._find_rect_index(x, y) is not None

    def _finish_reorder_drag(self, start_x: float, start_y: float, end_x: float, end_y: float) -> None:
        if self._reorder_callback is None:
            return
        source_index = self._find_rect_index(start_x, start_y)
        target_index = self._find_rect_index(end_x, end_y)
        if source_index is None:
            return
        if target_index is None:
            target_index = len(self._rects) - 1
            insert_after = True
        else:
            rect_x, rect_y, rect_w, rect_h = self._rects[target_index]
            # Drop after the target when the pointer lands in its lower/right half.
            insert_after = end_y > rect_y + rect_h / 2 or (
                rect_y <= end_y <= rect_y + rect_h and end_x > rect_x + rect_w / 2
            )
        self._reorder_callback(source_index, target_index, insert_after)

    def _handle_view_click(self, pos: QPoint, modifiers: object) -> None:
        if self.base_pixmap.isNull():
            return
        scene_pos = self.view.mapToScene(pos)
        clicked_index = self._find_rect_index(scene_pos.x(), scene_pos.y())
        if clicked_index is None:
            if modifiers & (Qt.ControlModifier | Qt.ShiftModifier):
                return
            self._selected_indexes = []
            self._last_selected_index = None
        elif modifiers & Qt.ShiftModifier:
            # Shift follows common list-selection behavior: select a contiguous
            # range from the last anchor, or add that range when Ctrl is also held.
            anchor = self._last_selected_index if self._last_selected_index is not None else clicked_index
            start = min(anchor, clicked_index)
            end = max(anchor, clicked_index)
            if modifiers & Qt.ControlModifier:
                merged = set(self._selected_indexes)
                merged.update(range(start, end + 1))
                self._selected_indexes = sorted(merged)
            else:
                self._selected_indexes = list(range(start, end + 1))
            self._last_selected_index = clicked_index
        elif modifiers & Qt.ControlModifier:
            if clicked_index in self._selected_indexes:
                self._selected_indexes.remove(clicked_index)
            else:
                self._selected_indexes.append(clicked_index)
                self._selected_indexes.sort()
            self._last_selected_index = clicked_index
        else:
            self._selected_indexes = [clicked_index]
            self._last_selected_index = clicked_index
        self._render_canvas()
        self._sync_region_list_selection()
        self._notify_selection_changed()

    def _find_rect_index(self, x: float, y: float) -> Optional[int]:
        for index, (rect_x, rect_y, rect_w, rect_h) in reversed(list(enumerate(self._rects))):
            if rect_x <= x <= rect_x + rect_w and rect_y <= y <= rect_y + rect_h:
                return index
        return None

    def _handle_region_list_selection(self) -> None:
        selected = [item.data(Qt.UserRole) for item in self.region_list.selectedItems()]
        if selected == self._selected_indexes:
            return
        self._selected_indexes = selected
        if selected:
            self._last_selected_index = selected[-1]
        self._render_canvas()
        self._notify_selection_changed()

    def _notify_selection_changed(self) -> None:
        if self._selection_callback is not None:
            self._selection_callback(list(self._selected_indexes))

    def _sync_region_list_selection(self) -> None:
        self.region_list.blockSignals(True)
        try:
            for row in range(self.region_list.count()):
                item = self.region_list.item(row)
                item.setSelected(item.data(Qt.UserRole) in self._selected_indexes)
        finally:
            self.region_list.blockSignals(False)

    def _build_inspector_text(
        self,
        image: Image.Image,
        rects: Sequence[Tuple[int, int, int, int]],
        description: str,
        info_cards: Sequence[Tuple[str, str]],
    ) -> str:
        covered_area = sum(w * h for _x, _y, w, h in rects)
        total_area = max(1, image.width * image.height)
        lines = [
            f"Canvas: {image.width} x {image.height}",
            f"Regions: {len(rects)}",
            f"Covered area: {covered_area} px",
            f"Coverage: {covered_area / total_area * 100:.2f}%",
            f"View: zoom mouse wheel, pan left-drag, Fit to reset",
            f"Summary: {description}",
        ]
        if info_cards:
            lines.append("")
            lines.append("Inspector")
            for title, value in info_cards:
                lines.append(f"{title}: {value}")
        if rects:
            sample = rects[:6]
            lines.append("")
            lines.append("Sample Regions")
            for index, (x, y, w, h) in enumerate(sample):
                lines.append(f"#{index:02d} x={x} y={y} w={w} h={h}")
            if len(rects) > len(sample):
                lines.append(f"... {len(rects) - len(sample)} more")
        return "\n".join(lines)
