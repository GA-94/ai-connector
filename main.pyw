import os
import sys

# Корень проекта — в sys.path и в рабочей папке: data/chats.db, repo/ и
# относительные пути всегда считаются от папки программы.
ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
try:
    os.chdir(ROOT)
except OSError:
    pass

# Free-threaded сборка Python (python3.14t.exe / pythonw3.14t.exe) роняет PyQt
# нарушением доступа 0xC0000005 - перехватить это нельзя, поэтому уходим на
# обычный интерпретатор ДО импорта PyQt6.
try:
    from app.interp_guard import ensure as guard_ensure

    if guard_ensure(script=os.path.abspath(__file__), purpose="main.pyw"):
        sys.exit(0)
except SystemExit:
    raise
except Exception as guard_error:  # noqa: BLE001
    print("Сторож интерпретатора: %s" % guard_error, file=sys.stderr)

# Мост PyQt5->PyQt6 ставим ДО краш-репортов и до импорта интерфейса: внутри
# crash.py есть ленивые импорты Qt (перехват qFatal), и им нужно старое имя
# модуля PyQt5. Неудача моста не смертельна — причину покажет main().
try:
    import app.qt_compat  # noqa: F401
except Exception as bridge_error:  # noqa: BLE001
    print("Мост PyQt5->PyQt6 не загрузился: %s" % bridge_error, file=sys.stderr)

# Краш-репорты ставим ДО импорта PyQt6. Раньше перехват включался уже после
# импорта интерфейса и создания QApplication — падение на импорте (или при
# создании окна) не оставляло отчёта, и папка crash_reports выглядела пустой.
try:
    from app.crash import install as install_crash

    install_crash("main.pyw")
except Exception as crash_error:  # noqa: BLE001 - без отчётов программа тоже работает
    print("Краш-репорты недоступны: " + str(crash_error), file=sys.stderr)


def main():
    try:
        # PyQt6 + мост совместимости: плоские константы Qt5, exec_(), QAction…
        from app.qt_compat.QtWidgets import QApplication
        from app.ui.main_window import MainWindow
    except Exception:
        import traceback

        traceback.print_exc()
        try:
            from app import crash

            crash.capture(*sys.exc_info(),
                          title="Ошибка импорта интерфейса",
                          source="main.pyw")
        except Exception:  # noqa: BLE001
            pass
        return 1

    try:
        from app.safe_app import SafeApplication

        app = SafeApplication(sys.argv)
    except Exception:  # noqa: BLE001 - защита интерфейса не должна мешать запуску
        app = QApplication(sys.argv)
    app.setApplicationName("AiConnector7")

    window = MainWindow()
    window.show()
    return int(app.exec())


if __name__ == "__main__":
    sys.exit(main())
