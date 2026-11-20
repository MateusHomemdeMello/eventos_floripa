"""Open the exported WebGIS through an HTTP server bound to this computer."""
import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote
import webbrowser


def main():
    parser = argparse.ArgumentParser(description='Abre o WebGIS exportado.')
    parser.add_argument('arquivo', nargs='?', default='qual_a_boa_floripa.html')
    parser.add_argument('--sem-navegador', action='store_true')
    args = parser.parse_args()
    output = Path(__file__).resolve().parent / 'data' / 'output'
    if Path(args.arquivo).name != args.arquivo or not (output / args.arquivo).is_file():
        parser.error('Selecione um HTML existente na pasta data/output.')
    handler = partial(SimpleHTTPRequestHandler, directory=output)
    with ThreadingHTTPServer(('127.0.0.1', 0), handler) as server:
        url = f'http://127.0.0.1:{server.server_port}/{quote(args.arquivo)}'
        print(url, flush=True)
        if not args.sem_navegador:
            webbrowser.open(url)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == '__main__':
    main()
