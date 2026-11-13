import 'enums.dart';
import 'json_utils.dart';

/// Отклонение результата валидатором (схема ValidationRejection).
class ValidationRejection {
  const ValidationRejection({
    required this.attemptNo,
    required this.reasons,
    this.category,
    this.fixesApplied = const <String>[],
    this.createdAt,
  });

  /// Номер попытки (1 или 2).
  final int attemptNo;

  /// Причины отклонения.
  final List<String> reasons;

  /// Категория отклонения.
  final RejectionCategory? category;

  /// Что Analyst исправил во второй итерации.
  final List<String> fixesApplied;

  /// Время создания записи об отклонении.
  final DateTime? createdAt;

  /// Создаёт модель из JSON-объекта контракта.
  factory ValidationRejection.fromJson(Map<String, dynamic> json) {
    return ValidationRejection(
      attemptNo: JsonUtils.asIntOr(json['attemptNo'], 1),
      reasons: JsonUtils.asStringList(json['reasons']),
      category: RejectionCategory.fromWire(json['category']),
      fixesApplied: JsonUtils.asStringList(json['fixesApplied']),
      createdAt: JsonUtils.asDateTime(json['createdAt']),
    );
  }
}
