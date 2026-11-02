"""서버가 지정한 PDF의 한 페이지를 메모리에서 PNG로 렌더링한다."""
import json
import sys
import pymupdf


def main():
    request = json.load(sys.stdin)
    with pymupdf.open(request['pdf']) as document:
        page = document[request['page'] - 1]
        for locator in request['locators']:
            if (locator.get('coordinate_space') != 'pymupdf_unrotated_page'
                    or locator.get('units') != 'pt'):
                continue
            page.draw_rect(pymupdf.Rect(locator['bbox']), color=(0.04, 0.58, 0.45),
                           fill=(0.14, 0.75, 0.57), fill_opacity=0.15, width=0.7,
                           overlay=True)
        sys.stdout.buffer.write(page.get_pixmap(matrix=pymupdf.Matrix(1.6, 1.6),
                                               alpha=False).tobytes('png'))


if __name__ == '__main__':
    main()
