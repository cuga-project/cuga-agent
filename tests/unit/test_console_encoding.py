import io

from cuga.cli.main import _configure_stream_encoding


def test_gbk_stream_supports_unicode_output():
    stream = io.TextIOWrapper(
        io.BytesIO(),
        encoding="gbk",
    )

    try:
        _configure_stream_encoding(stream)

        message = "CRM services are running ✅"
        stream.write(message)
        stream.flush()

        output = stream.buffer.getvalue().decode("utf-8")
        assert output == message
    finally:
        stream.close()


def test_stream_without_reconfigure_is_supported():
    class StreamWithoutReconfigure:
        pass

    _configure_stream_encoding(StreamWithoutReconfigure())
