import '../../core/api_client.dart';

/// Скачивание файла на платформах без `dart:js_interop` (VM, тесты).
///
/// Приложение работает в браузере, поэтому здесь только явная ошибка вместо
/// тихого отказа: на web используется реализация из `download_file_web.dart`.
void triggerBrowserDownload(DownloadedFile file) {
  throw UnsupportedError(
    'Скачивание файлов доступно только в браузере',
  );
}
