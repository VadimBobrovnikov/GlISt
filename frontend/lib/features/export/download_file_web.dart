import 'dart:js_interop';

import 'package:web/web.dart' as web;

import '../../core/api_client.dart';

/// Скачивание файла в браузере: Blob + временная ссылка `<a download>`.
void triggerBrowserDownload(DownloadedFile file) {
  final web.Blob blob = web.Blob(
    <JSAny>[file.bytes.toJS].toJS,
    web.BlobPropertyBag(type: file.mimeType),
  );
  final String url = web.URL.createObjectURL(blob);
  final web.HTMLAnchorElement anchor = web.HTMLAnchorElement()
    ..href = url
    ..download = file.filename
    ..style.display = 'none';
  web.document.body?.appendChild(anchor);
  anchor.click();
  anchor.remove();
  web.URL.revokeObjectURL(url);
}
