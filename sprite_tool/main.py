import sys

from PyQt5.QtWidgets import QApplication

from sprite_tool.ui.main_window import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    return app.exec_()
