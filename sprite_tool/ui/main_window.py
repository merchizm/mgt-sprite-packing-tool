from PyQt5.QtCore import Qt, QUrl
from PyQt5.QtGui import QDesktopServices
from PyQt5.QtWidgets import QAction, QApplication, QDialog, QDialogButtonBox, QMainWindow, QMenu, QPushButton, QTabWidget, QTextBrowser, QToolBar, QToolButton, QVBoxLayout
import qdarkstyle

from sprite_tool.constants import APP_NAME, APP_VERSION, REPOSITORY_URL
from sprite_tool.ui.generate_tab import GenerateSheetTab
from sprite_tool.ui.split_tab import SplitSheetTab


class AboutDialog(QDialog):
    def __init__(self, parent: QMainWindow) -> None:
        super().__init__(parent)
        self.setWindowTitle("About")
        info = QTextBrowser()
        repo_text = REPOSITORY_URL if REPOSITORY_URL else "Repository URL is not configured yet."
        info.setHtml(
            f"""
            <h3>{APP_NAME}</h3>
            <p><b>Version:</b> {APP_VERSION}</p>
            <p><b>Repository:</b> {repo_text}</p>
            <p>This tool creates PixiJS-compatible atlases, splits existing sheets, previews regions, and exports PNG/JSON/KTX2 assets.</p>
            """
        )
        open_repo_button = QPushButton("Open Repository")
        open_repo_button.setEnabled(bool(REPOSITORY_URL))
        open_repo_button.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(REPOSITORY_URL)))

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)

        layout = QVBoxLayout(self)
        layout.addWidget(info)
        layout.addWidget(open_repo_button)
        layout.addWidget(buttons)


class HowToUseDialog(QDialog):
    def __init__(self, parent: QMainWindow) -> None:
        super().__init__(parent)
        self.setWindowTitle("How to Use")
        self.resize(720, 560)
        info = QTextBrowser()
        info.setHtml(
            f"""
            <h3>{APP_NAME}</h3>
            <h4>Build Sheet</h4>
            <ol>
              <li>Add source sprites with <b>Add Files</b> or drag image files into the source list.</li>
              <li>Reorder sprites in the list or drag sprites directly on the atlas preview.</li>
              <li>Use <b>Add Spacer</b> and <b>Tools &gt; Set Spacer Color</b> to create visual category gaps. Spacer color applies to every spacer and can be transparent.</li>
              <li>Select a sprite or spacer in the preview before adding a spacer to insert it after that region.</li>
              <li>Select packing, padding, prescale, downscale, pixel format, and optimization settings.</li>
              <li>Use <b>Export...</b> to write the PNG atlas, PixiJS JSON, and optional KTX2 output.</li>
            </ol>
            <h4>Sprite Background Tools</h4>
            <ul>
              <li><b>Tools &gt; Toggle Checker Background</b> toggles a preview-only transparency checkerboard.</li>
              <li><b>Tools &gt; Set Atlas Background Color</b> fills sprite cells in the exported atlas.</li>
              <li><b>Tools &gt; Remove Sprite Background</b> opens a sprite preview dialog. Choose a sprite, click a pixel, or type a source color and tolerance; the chosen background color is removed from all sprites.</li>
              <li><b>Tools &gt; Replace Sprite Background</b> uses the same picker flow and replaces the chosen source color across all sprites.</li>
            </ul>
            <h4>Split Sheet</h4>
            <ol>
              <li>Select an existing sheet PNG.</li>
              <li>Choose grid or alpha detection settings and inspect detected regions.</li>
              <li>Select regions when needed, then export sprites and JSON.</li>
            </ol>
            <h4>Shortcuts and Mouse Controls</h4>
            <ul>
              <li><b>Mouse wheel</b>: zoom preview.</li>
              <li><b>Left-drag empty preview area</b>: pan preview.</li>
              <li><b>Click sprite/region</b>: select it.</li>
              <li><b>Click spacer</b>: select the spacer so it can be colored, used as an insertion target, or dragged to reorder.</li>
              <li><b>Ctrl + click</b>: add or remove a sprite/region from selection.</li>
              <li><b>Shift + click</b>: select a range from the last selected sprite/region.</li>
              <li><b>Ctrl + Shift + click</b>: add a range to the current selection.</li>
              <li><b>Left-drag sprite in Build Sheet preview</b>: reorder it in the atlas/source list.</li>
              <li><b>Fit</b>: reset preview zoom and framing.</li>
            </ul>
            """
        )
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)

        layout = QVBoxLayout(self)
        layout.addWidget(info)
        layout.addWidget(buttons)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.dark_mode_enabled = True
        self.setWindowTitle(APP_NAME)
        self.resize(1280, 820)
        self._build_ui()
        self.apply_theme()

    def _build_ui(self) -> None:
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.setTabPosition(QTabWidget.North)
        self.tabs.setMovable(True)
        self.tabs.addTab(GenerateSheetTab(), "Build Sheet")
        self.tabs.addTab(SplitSheetTab(), "Split Sheet")
        self.setCentralWidget(self.tabs)

        toolbar = QToolBar("Application", self)
        toolbar.setMovable(False)
        self.addToolBar(Qt.TopToolBarArea, toolbar)

        help_menu = QMenu("Help", self)
        about_action = QAction("About", self)
        how_to_use_action = QAction("How to Use", self)
        about_action.triggered.connect(self.open_about)
        how_to_use_action.triggered.connect(self.open_how_to_use)
        help_menu.addAction(about_action)
        help_menu.addAction(how_to_use_action)

        help_button = QToolButton(self)
        help_button.setText("Help")
        help_button.setMenu(help_menu)
        help_button.setPopupMode(QToolButton.InstantPopup)
        toolbar.addWidget(help_button)

    def apply_theme(self) -> None:
        stylesheet = qdarkstyle.load_stylesheet_pyqt5() if self.dark_mode_enabled else ""
        QApplication.instance().setStyleSheet(stylesheet)
        self.setStyleSheet(
            """
            QTabWidget::pane {
                border: 0;
                background: transparent;
            }
            QTabBar::tab {
                background: #19232d;
                color: #b8c7d6;
                padding: 12px 18px;
                margin-right: 2px;
                border-top-left-radius: 6px;
                border-top-right-radius: 6px;
            }
            QTabBar::tab:selected {
                background: #32414b;
                color: #f0f6fc;
            }
            """
        )

    def open_about(self) -> None:
        AboutDialog(self).exec_()

    def open_how_to_use(self) -> None:
        HowToUseDialog(self).exec_()
