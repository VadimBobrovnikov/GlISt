import 'json_utils.dart';

/// Детали валидации по полю (схема Error.details).
class ApiErrorDetail {
  const ApiErrorDetail({
    required this.message,
    this.field,
  });

  /// Сообщение об ошибке.
  final String message;

  /// Имя поля, к которому относится ошибка.
  final String? field;

  /// Создаёт модель из JSON-объекта.
  factory ApiErrorDetail.fromJson(Map<String, dynamic> json) {
    return ApiErrorDetail(
      message: JsonUtils.asStringOr(json['message'], ''),
      field: JsonUtils.asString(json['field']),
    );
  }
}

/// Унифицированная ошибка API (схема Error из contracts/api.yaml).
class ApiError {
  const ApiError({
    this.code = '',
    this.message = '',
    this.details = const <ApiErrorDetail>[],
  });

  /// Машинный код ошибки.
  final String code;

  /// Человекочитаемое сообщение.
  final String message;

  /// Детали валидации по полям.
  final List<ApiErrorDetail> details;

  /// Создаёт модель из JSON-объекта контракта.
  factory ApiError.fromJson(Map<String, dynamic> json) {
    return ApiError(
      code: JsonUtils.asStringOr(json['code'], ''),
      message: JsonUtils.asStringOr(json['message'], ''),
      details: JsonUtils.asMapList(json['details'])
          .map(ApiErrorDetail.fromJson)
          .toList(growable: false),
    );
  }
}

/// Исключение HTTP-слоя с кодом состояния для обработки 409/410.
class ApiException implements Exception {
  const ApiException({
    required this.message,
    this.statusCode,
    this.code = 'http_error',
    this.details = const <ApiErrorDetail>[],
  });

  /// HTTP-код ответа (если он был получен).
  final int? statusCode;

  /// Машинный код ошибки.
  final String code;

  /// Человекочитаемое сообщение.
  final String message;

  /// Детали валидации по полям.
  final List<ApiErrorDetail> details;

  /// `true`, если код 409 Conflict (результат ещё не готов).
  bool get isConflict => statusCode == 409;

  /// `true`, если код 410 Gone (артефакт удалён по политике хранения).
  bool get isGone => statusCode == 410;

  /// Создаёт исключение из ответа сервера.
  factory ApiException.fromResponse(int? statusCode, Object? data) {
    final ApiError error = ApiError.fromJson(JsonUtils.asMap(data));
    return ApiException(
      statusCode: statusCode,
      code: error.code.isEmpty ? 'http_error' : error.code,
      message: error.message.isEmpty ? 'Ошибка запроса к API' : error.message,
      details: error.details,
    );
  }

  @override
  String toString() => 'ApiException($statusCode, $code): $message';
}
