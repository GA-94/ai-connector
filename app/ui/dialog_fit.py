"""Адаптивный размер диалогов: окно не должно уезжать за низ экрана.

Проблема была в жёстком resize(680, 900): на ноутбуке с экраном 768 по высоте
низ диалога (кнопки «Сохранить») оказывался за пределами экрана. Теперь размер
считается от доступной области экрана: сначала урезаем высоту, а освободившееся
место добираем шириной (диалоги показывают содержимое в две колонки).
"""

from app.qt_compat.QtWidgets import QApplication

MARGIN = 32          # запас, чтобы окно не упиралось в край экрана
DEFAULT_WIDTH = 980
DEFAULT_HEIGHT = 760
MIN_WIDTH = 560
MIN_HEIGHT = 360


def screen_rect(widget=None):
    """Доступная область экрана, на котором виджет (или основной экран)."""
    screen = None
    if widget is not None:
        try:
            handle = widget.windowHandle()
            if handle is not None:
                screen = handle.screen()
        except (AttributeError, RuntimeError):
            screen = None
        if screen is None:
            try:
                screen = widget.screen()
            except (AttributeError, RuntimeError):
                screen = None
    if screen is None:
        screen = QApplication.primaryScreen()
    if screen is None:
        return None
    return screen.availableGeometry()


def fit_size(width=DEFAULT_WIDTH, height=DEFAULT_HEIGHT, rect=None,
             min_width=MIN_WIDTH, min_height=MIN_HEIGHT):
    """Посчитать размер окна, который точно влезает в экран.

    Возвращает (ширина, высота, максимальная ширина, максимальная высота).
    """
    if rect is None:
        return int(width), int(height), 16777215, 16777215
    avail_w = max(320, int(rect.width()) - MARGIN)
    avail_h = max(240, int(rect.height()) - MARGIN)

    want_w = min(int(width), avail_w)
    want_h = min(int(height), avail_h)
    # Высота урезана — значит экран низкий: занимаем ширину, чтобы содержимое
    # разложилось в колонки и влезло по высоте.
    if want_h < int(height) and want_w < avail_w:
        want_w = min(avail_w, max(want_w, int(avail_w * 0.96)))
    # Очень низкий экран: ширина компенсирует нехватку высоты.
    if avail_h < int(height):
        want_w = min(avail_w, max(want_w, int(avail_w * 0.9)))

    want_w = max(min(want_w, avail_w), min(int(min_width), avail_w))
    want_h = max(min(want_h, avail_h), min(int(min_height), avail_h))
    return int(want_w), int(want_h), avail_w, avail_h


def fit_dialog(dialog, width=DEFAULT_WIDTH, height=DEFAULT_HEIGHT,
               min_width=MIN_WIDTH, min_height=MIN_HEIGHT):
    """Подогнать уже созданный диалог под экран (вызывать после сборки UI)."""
    rect = screen_rect(dialog)
    size_w, size_h, max_w, max_h = fit_size(
        width, height, rect, min_width=min_width, min_height=min_height)
    if rect is not None:
        dialog.setMaximumSize(max_w, max_h)
    dialog.resize(size_w, size_h)
    return dialog.size()
