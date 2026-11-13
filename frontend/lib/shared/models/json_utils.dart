/// Утилиты безопасного разбора JSON-значений.
///
/// Анализатор настроен на `strict-casts: true`, поэтому неявные приведения
/// `dynamic` запрещены: все значения проверяются через `is` и приводятся
/// к ожидаемому типу явно.
class JsonUtils {
  const JsonUtils._();

  /// Возвращает строку или `null`, если значение не строка.
  static String? asString(Object? value) => value is String ? value : null;

  /// Возвращает строку или [fallback] для нестроковых значений.
  static String asStringOr(Object? value, String fallback) =>
      value is String ? value : fallback;

  /// Возвращает целое число или `null`.
  static int? asInt(Object? value) {
    if (value is int) {
      return value;
    }
    if (value is num) {
      return value.toInt();
    }
    return null;
  }

  /// Возвращает целое число или [fallback].
  static int asIntOr(Object? value, int fallback) => asInt(value) ?? fallback;

  /// Возвращает число с плавающей точкой или `null`.
  static double? asDouble(Object? value) {
    if (value is num) {
      return value.toDouble();
    }
    return null;
  }

  /// Возвращает число с плавающей точкой или [fallback].
  static double asDoubleOr(Object? value, double fallback) =>
      asDouble(value) ?? fallback;

  /// Возвращает булево значение или [fallback].
  static bool asBoolOr(Object? value, bool fallback) =>
      value is bool ? value : fallback;

  /// Разбирает дату/время ISO-8601 из строки.
  static DateTime? asDateTime(Object? value) {
    if (value is String) {
      return DateTime.tryParse(value);
    }
    return null;
  }

  /// Возвращает JSON-объект или пустую карту.
  static Map<String, dynamic> asMap(Object? value) {
    if (value is Map<String, dynamic>) {
      return value;
    }
    return const <String, dynamic>{};
  }

  /// Возвращает JSON-массив или пустой список.
  static List<dynamic> asList(Object? value) {
    if (value is List<dynamic>) {
      return value;
    }
    return const <dynamic>[];
  }

  /// Возвращает список JSON-объектов (элементы иного типа отбрасываются).
  static List<Map<String, dynamic>> asMapList(Object? value) =>
      asList(value).whereType<Map<String, dynamic>>().toList(growable: false);

  /// Возвращает список строк (элементы иного типа отбрасываются).
  static List<String> asStringList(Object? value) =>
      asList(value).whereType<String>().toList(growable: false);
}
