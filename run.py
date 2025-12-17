from gevent import monkey
monkey.patch_all()

from app import create_app
from gevent.pywsgi import WSGIServer

app = create_app()

if __name__ == '__main__':
    # 使用 gevent WSGIServer 以更好地支援 SSE，並明確指定監聽 127.0.0.1
    # 關閉 gevent 預設的 HTTP access log，避免終端機一直刷 request 訊息
    http_server = WSGIServer(('127.0.0.1', 5000), app, log=None)
    print("服務器已啟動於 http://127.0.0.1:5000")
    print("使用 gevent WSGIServer...")
    http_server.serve_forever() 