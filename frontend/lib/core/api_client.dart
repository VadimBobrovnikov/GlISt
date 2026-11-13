import 'package:dio/dio.dart';

import '../shared/models/models.dart';

/// HTTP-клиент GlISt API на пакете `dio`.
///
/// Базовый URL по умолчанию — локальный стенд `http://localhost:8000`.
/// Ответы 4xx/5xx преобразуются в [ApiException]; специальные коды
/// 409 (результат не готов) и 410 (артефакт удалён) распознаются через
/// [ApiException.isConflict] и [ApiException.isGone].
class ApiClient {
  /// Создаёт клиент; можно передать готовый [Dio] (например, в тестах).
  ApiClient({Dio? dio, String baseUrl = 'http://localhost:8000'})
      : _dio = dio ??
            Dio(
              BaseOptions(
                baseUrl: baseUrl,
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

  /// Выполняет запрос и приводит ошибки `dio` к [ApiException].
  Future<Response<dynamic>> _send(
    Future<Response<dynamic>> Function() request,
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
