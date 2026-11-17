from PIL import Image
from PySide6.QtCore import Qt, QPointF
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialog
from teddystudio.ui_crop import CropCanvas, CropDialog


def drag(canvas, start, end):
    QTest.mousePress(
        canvas, Qt.MouseButton.LeftButton, pos=canvas.to_view(QPointF(*start)).toPoint()
    )
    QTest.mouseMove(canvas, canvas.to_view(QPointF(*end)).toPoint())
    QTest.mouseRelease(
        canvas, Qt.MouseButton.LeftButton, pos=canvas.to_view(QPointF(*end)).toPoint()
    )


def test_draw_move_resize_in_image_coordinates(app):
    canvas = CropCanvas(Image.new("RGB", (1000, 600), "blue"))
    canvas.resize(740, 500)
    canvas.show()
    app.processEvents()
    canvas.set_square(False)
    drag(canvas, (30, 30), (350, 230))
    box = canvas.crop_box()
    assert all(abs(a - b) < 5 for a, b in zip(box, (30, 30, 350, 230)))
    drag(canvas, (180, 120), (280, 170))
    box = canvas.crop_box()
    assert all(abs(a - b) < 7 for a, b in zip(box, (130, 80, 450, 280)))
    drag(canvas, (box[2], box[3]), (700, 450))
    assert canvas.crop_box()[2] > 690 and canvas.crop_box()[3] > 440
    before = canvas.crop_box()
    canvas.resize(1000, 680)
    app.processEvents()
    assert canvas.crop_box() == before
    canvas.close()


def test_square_selection_and_moving_stay_inside_image(app):
    canvas = CropCanvas(Image.new("RGB", (900, 500), "blue"))
    canvas.resize(800, 600)
    canvas.show()
    app.processEvents()
    drag(canvas, (20, 20), (250, 180))
    box = canvas.crop_box()
    assert abs((box[2] - box[0]) - (box[3] - box[1])) <= 1
    center = canvas.selection.center()
    drag(canvas, (center.x(), center.y()), (900, 500))
    left, top, right, bottom = canvas.crop_box()
    assert 0 <= left < right <= 900 and 0 <= top < bottom <= 500
    QTest.keyClick(canvas, Qt.Key.Key_Right, Qt.KeyboardModifier.ShiftModifier)
    assert canvas.crop_box()[2] <= 900
    canvas.close()


def test_crop_dialog_preview_and_cancel_do_not_modify_source(app, tmp_path):
    path = tmp_path / "original.png"
    Image.new("RGB", (800, 500), "orange").save(path)
    original = path.read_bytes()
    dialog = CropDialog(Image.open(path).convert("RGB"))
    dialog.show()
    app.processEvents()
    assert not dialog.preview.pixmap().isNull()
    dialog.reject()
    assert dialog.result() == QDialog.DialogCode.Rejected
    assert path.read_bytes() == original


def test_cover_page_applies_crop_only_after_accepting(app, tmp_path, monkeypatch):
    from PySide6.QtCore import QRectF
    from teddystudio.domain import Book
    from teddystudio.storage import Store, Settings
    from teddystudio.ui_library import LibraryPage
    from teddystudio.ui_covers import CoversPage

    monkeypatch.setenv("TEDDYSTUDIO_DATA", str(tmp_path / "data"))
    source = tmp_path / "original.png"
    Image.new("RGB", (800, 500), "orange").save(source)
    original = source.read_bytes()
    store = Store(tmp_path / "test.sqlite")
    store.save_book(Book("1", str(tmp_path), "Cover", cover=str(source)))
    page = CoversPage(LibraryPage(store, Settings()), None)
    page.list.setCurrentRow(0)

    def accept(dialog):
        dialog.canvas.set_square(False)
        dialog.canvas.selection = QRectF(100, 50, 300, 200)
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(CropDialog, "exec", accept)
    page.crop()
    assert Image.open(page.pending).size == (300, 200)
    assert source.read_bytes() == original
    assert page.book.cover == str(source)
    page.save()
    assert store.books()[0].cover_confirmed
    assert store.books()[0].cover == page.pending
