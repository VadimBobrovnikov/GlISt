import 'dart:typed_data';

import 'package:dio/dio.dart';

import '../shared/models/models.dart';

/// Скачанный файл артефакта.
class DownloadedFile {
  const DownloadedFile({
    required this.bytes,
    required this.filename,
    required this.mimeType,
  });

  final Uint8List bytes;
  final String filename;
  final String mimeType;
}

/// HTTP-клиент GlISt API на пакете `dio`.
///
/// Базовый URL задаётся `--dart-define=API_BASE_URL=...`; по умолчанию —
/// локальный стенд `http://localhost:8000`.
class ApiClient {
  /// Базовый URL API из окружения сборки.
  static const String _defaultBaseUrl = String.fromEnvironment(
    'API_BASE_URL',
    defaultValue: 'http://localhost:8000',
  );

  /// Создаёт клиент; можно передать готовый [Dio] (например, в тестах).
  ApiClient({Dio? dio, String? baseUrl})
      : _dio = dio ??
            Dio(
              BaseOptions(
                baseUrl: baseUrl ?? _defaultBaseUrl,
                connectTimeout: const Duration(seconds: 10),
                sendTimeout: const Duration(seconds: 30),
                receiveTimeout: const Duration(seconds: 30),
                headers: const <String, dynamic>{
                  'Content-Type': 'application/json',
                  'Accept': 'application/json',
                },
              ),
            );

  final Dio _dio;

  /// Доступ к базовому [Dio] (для настройки интерсепторов извне).
  Dio get dio => _dio;

  /// POST /api/v1/queries — создание запроса и запуска анализа.
  ///
  /// Возвращает [QueryCreated] с запросом и запуском; при необходимости
  /// уточнения поле [QueryCreated.clarification] будет заполнено.
  Future<QueryCreated> createQuery({
    required String text,
    String? parentQueryId,
    String? mode,
  }) async {
    final Map<String, dynamic> payload = <String, dynamic>{
      'text': text,
      'parentQueryId': ?parentQueryId,
      'mode': ?mode,
    };
    final Response<dynamic> response = await _send(
      () => _dio.post<dynamic>('/api/v1/queries', data: payload),
    );
    return QueryCreated.fromJson(JsonUtils.asMap(response.data));
  }

  /// GET /api/v1/queries — история запросов с курсорной пагинацией.
  Future<QueryPage> listQueries({int limit = 20, String? cursor}) async {
    final Response<dynamic> response = await _send(
      () => _dio.get<dynamic>(
        '/api/v1/queries',
        queryParameters: <String, dynamic>{
          'limit': limit,
          'cursor': ?cursor,
        },
      ),
    );
    return QueryPage.fromJson(JsonUtils.asMap(response.data));
  }

  /// GET /api/v1/runs/{runId} — состояние запуска.
  Future<Run> getRun(String runId) async {
    final Response<dynamic> response = await _send(
      () => _dio.get<dynamic>('/api/v1/runs/$runId'),
    );
    return Run.fromJson(JsonUtils.asMap(response.data));
  }

  /// GET /api/v1/runs/{runId}/result — результат анализа.
  ///
  /// Пока результат не готов, сервер отвечает 409 Conflict: клиент бросает
  /// [ApiException] с [ApiException.isConflict] == `true`.
  Future<AnalysisResult> getRunResult(String runId) async {
    final Response<dynamic> response = await _send(
      () => _dio.get<dynamic>('/api/v1/runs/$runId/result'),
    );
    return AnalysisResult.fromJson(JsonUtils.asMap(response.data));
  }

  /// POST /api/v1/runs/{runId}/exports — экспорт результата в PDF или GeoJSON.
  ///
  /// Возвращает [Artifact] со ссылкой на скачивание; 410 Gone означает, что
  /// артефакт удалён по политике хранения ([ApiException.isGone]).
  Future<Artifact> createExport(String runId, ExportFormat format) async {
    final Response<dynamic> response = await _send(
      () => _dio.post<dynamic>(
        '/api/v1/runs/$runId/exports',
        data: <String, dynamic>{'format': format.wireValue},
      ),
    );
    return Artifact.fromJson(JsonUtils.asMap(response.data));
  }

  /// Абсолютный URL по пути API (без жёсткого localhost).
  String absoluteUrl(String path) {
    if (path.startsWith('http://') || path.startsWith('https://')) {
      return path;
    }
    final String base = _dio.options.baseUrl.replaceAll(RegExp(r'/+$'), '');
    final String suffix = path.startsWith('/') ? path : '/$path';
    return '$base$suffix';
  }

  /// URL RGB-композита до/после из сцен, участвовавших в расчёте.
  String previewUrl(String runId, String period) =>
      absoluteUrl('/api/v1/runs/$runId/preview?period=$period');

  /// NDVI до/после и класс перехода для точки.
  Future<Map<String, dynamic>> sampleRun(
    String runId,
    double lat,
    double lon,
  ) async {
    final Response<dynamic> response = await _send(
      () => _dio.get<dynamic>(
        '/api/v1/runs/$runId/sample',
        queryParameters: <String, dynamic>{'lat': lat, 'lon': lon},
      ),
    );
    return JsonUtils.asMap(response.data);
  }

  /// Скачивает содержимое артефакта (Content-Disposition → имя файла).
  Future<DownloadedFile> downloadArtifact(String contentUrl) async {
    final Response<List<int>> response = await _send(
      () => _dio.get<List<int>>(
        contentUrl,
        options: Options(
          responseType: ResponseType.bytes,
          receiveTimeout: const Duration(seconds: 60),
          followRedirects: true,
        ),
      ),
    );
    final List<int> bytes = response.data ?? <int>[];
    final String? disposition =
        response.headers.value('content-disposition');
    final String? contentType = response.headers.value('content-type');
    return DownloadedFile(
      bytes: Uint8List.fromList(bytes),
      filename:
          _filenameFromDisposition(disposition) ?? 'glist-export.bin',
      mimeType: contentType ?? 'application/octet-stream',
    );
  }

  String? _filenameFromDisposition(String? disposition) {
    if (disposition == null) {
      return null;
    }
    final RegExpMatch? match =
        RegExp(r'filename="?([^";]+)"?', caseSensitive: false)
            .firstMatch(disposition);
    final String? raw = match?.group(1);
    if (raw == null || raw.trim().isEmpty) {
      return null;
    }
    return Uri.decodeComponent(raw.trim().replaceAll('"', ''));
  }

  /// Выполняет запрос и приводит ошибки `dio` к [ApiException].
  Future<Response<T>> _send<T>(
    Future<Response<T>> Function() request,
  ) async {
    try {
      return await request();
    } on DioException catch (error) {
      throw ApiException.fromResponse(
        error.response?.statusCode,
        error.response?.data,
      );
    }
  }
}
