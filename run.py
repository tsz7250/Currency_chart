from gevent import monkey
monkey.patch_all()

import os
from app import create_app
from gevent.pywsgi import WSGIServer

app = create_app()

if __name__ == '__main__':
    # 透過環境變數控制監聽位址（Docker 內設為 0.0.0.0，本機開發預設 127.0.0.1）
    host = os.environ.get('HOST', '127.0.0.1')
    port = int(os.environ.get('PORT', '5000'))
    
    # 使用 gevent WSGIServer 以更好地支援 SSE
    # 關閉 gevent 預設的 HTTP access log，避免終端機一直刷 request 訊息
    http_server = WSGIServer((host, port), app, log=None)
    print(f"服務器已啟動於 http://{host}:{port}")
    print("使用 gevent WSGIServer...")
    http_server.serve_forever()     