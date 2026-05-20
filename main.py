#!/usr/bin/env python3
"""
Bethesda Strings AI Translator
Translate .strings/.dlstrings/.ilstrings files using local Ollama AI
"""
import sys
import logging
from pathlib import Path
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QLocale, Qt, QTranslator
from gui.main_window import MainWindow
from gui.theme_manager import ThemeManager
from gui.app_settings import load_settings, apply_theme, CONFIG_FILENAME, get_config_path

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('translator.log', encoding='utf-8')
    ]
)
logger = logging.getLogger(__name__)

# Global theme manager instance
theme_manager = ThemeManager()


def main():
    """Main entry point for the application."""
    # Enable high-DPI scaling
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    # Force Qt's own file dialogs instead of native ones.
    # Native dialogs (GTK/KDE portal) can deadlock the Qt event loop on Linux,
    # making the app completely unresponsive and blocking the compositor's focus
    # grab — reproducible on tiling WMs (i3, sway, Hyprland) and mixed DE setups.
    # This must be set before QApplication is created.
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_DontUseNativeDialogs)

    app = QApplication(sys.argv)
    app.setApplicationName("Bethesda Strings AI Translator")
    app.setApplicationVersion("0.1.0")
    app.setOrganizationName("BethesdaModTools")

    # Load settings (JSON config > QSettings > defaults) with env overrides
    settings = load_settings()
    logger.info(f"Config loaded from {get_config_path()}")

    # Apply saved theme
    apply_theme(app, settings.theme)

    # Setup translator
    translator = QTranslator()
    if settings.ui_language == "Ukrainian":
        # First try to load from the gui/translations directory
        translations_path = Path(__file__).parent / "gui" / "translations" / "uk_UA.qm"
        if translations_path.exists():
            if translator.load(str(translations_path)):
                app.installTranslator(translator)
                QLocale.setDefault(QLocale(QLocale.Ukrainian, QLocale.Ukraine))
                logger.info("Ukrainian localization loaded")
            else:
                logger.error(f"Failed to load translation file: {translations_path}")
        else:
            logger.warning(f"Translation file not found: {translations_path}")
    else:
        # Set locale for proper string handling
        QLocale.setDefault(QLocale(QLocale.English, QLocale.UnitedStates))

    window = MainWindow(settings=settings, theme_manager=theme_manager)

    # Set application icon
    icon_path = Path(__file__).parent / "resources" / "app_icon.png"
    if icon_path.exists():
        from PySide6.QtGui import QIcon
        icon = QIcon(str(icon_path))
        app.setWindowIcon(icon)
        window.setWindowIcon(icon)

    window.show()

    # Process events once to ensure thread starts properly
    app.processEvents()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
