# -*- coding: utf-8 -*-
"""
Тесты модуля file_processor.py и загрузчика документов core/document_loader.py.
"""

import os
import tempfile
from PIL import Image
from scan_reader.file_processor import FileProcessor
from scan_reader.core.document_loader import load_document


def test_image_processing_normalization():
    proc = FileProcessor(max_dimension=1000)
    # Создаем временное тестовое изображение
    img = Image.new("RGB", (2000, 1500), color="blue")
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tf:
        tmp_path = tf.name
        img.save(tmp_path)

    try:
        uris = proc.process_image_file(tmp_path)
        assert len(uris) == 1
        assert uris[0].startswith("data:image/jpeg;base64,")

        mode, content = proc.prepare_document_inputs(tmp_path)
        assert mode == "vision"
        assert len(content) == 1
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


def test_text_document_loader():
    with tempfile.NamedTemporaryFile(suffix=".txt", mode="w", encoding="utf-8", delete=False) as tf:
        tf.write("Судебный приказ от 10.03.2020 № 2-123\nВзыскать с должника 5000 руб.")
        tmp_path = tf.name

    try:
        title, text = load_document(tmp_path)
        assert "Судебный приказ" in text
        assert "5000 руб" in text

        proc = FileProcessor()
        mode, content = proc.prepare_document_inputs(tmp_path)
        assert mode == "text"
        assert isinstance(content, str)
        assert "5000 руб" in content
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
