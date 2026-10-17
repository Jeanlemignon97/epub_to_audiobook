from audiobook_generator.queue.naming import safe_book_name


def test_safe_book_name_is_readable_and_portable():
    assert safe_book_name("Croissance de l’église") == "Croissance_de_l_eglise"
