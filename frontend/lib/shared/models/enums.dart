/// Перечисления домена GlISt, соответствующие `contracts/api.yaml`.
library;

/// Статус запроса и запуска анализа (FR-020).
enum RunStatus {
  waiting('ожидает'),
  processing('в обработке'),
  completed('завершён'),
  validationFailed('не прошёл проверку'),
  noData('данные недоступны'),
  error('ошибка'),
  unknown('неизвестно');

  const RunStatus(this.wireValue);

  /// Значение статуса в контракте API.
  final String wireValue;

  /// Разбирает строку контракта; неизвестное значение — [unknown].
  static RunStatus fromWire(Object? value) {
    if (value is String) {
      for (final RunStatus status in RunStatus.values) {
        if (status.wireValue == value) {
          return status;
        }
      }
    }
    return RunStatus.unknown;
  }
}

/// Категория отклонения результата валидатором.
enum RejectionCategory {
  analysisError('analysis_error'),
  dataQuality('data_quality');

  const RejectionCategory(this.wireValue);

  /// Значение категории в контракте API.
  final String wireValue;

  /// Разбирает строку контракта; неизвестное значение — `null`.
  static RejectionCategory? fromWire(Object? value) {
    if (value is String) {
      for (final RejectionCategory category in RejectionCategory.values) {
        if (category.wireValue == value) {
          return category;
        }
      }
    }
    return null;
  }
}

/// Язык запроса.
enum QueryLanguage {
  ru('ru'),
  en('en');

  const QueryLanguage(this.wireValue);

  /// Значение языка в контракте API.
  final String wireValue;

  /// Разбирает строку контракта; неизвестное значение — [ru].
  static QueryLanguage fromWire(Object? value) {
    if (value is String) {
      for (final QueryLanguage language in QueryLanguage.values) {
        if (language.wireValue == value) {
          return language;
        }
      }
    }
    return QueryLanguage.ru;
  }
}

/// Вид запроса: первичный или уточняющий.
enum QueryKind {
  primary('primary'),
  followUp('follow_up');

  const QueryKind(this.wireValue);

  /// Значение вида запроса в контракте API.
  final String wireValue;

  /// Разбирает строку контракта; неизвестное значение — [primary].
  static QueryKind fromWire(Object? value) {
    if (value is String) {
      for (final QueryKind kind in QueryKind.values) {
        if (kind.wireValue == value) {
          return kind;
        }
      }
    }
    return QueryKind.primary;
  }
}

/// Тип уточняющего вопроса.
enum ClarificationKind {
  ambiguity('ambiguity'),
  noDataAlternatives('no_data_alternatives');

  const ClarificationKind(this.wireValue);

  /// Значение типа уточнения в контракте API.
  final String wireValue;

  /// Разбирает строку контракта; неизвестное значение — [ambiguity].
  static ClarificationKind fromWire(Object? value) {
    if (value is String) {
      for (final ClarificationKind kind in ClarificationKind.values) {
        if (kind.wireValue == value) {
          return kind;
        }
      }
    }
    return ClarificationKind.ambiguity;
  }
}

/// Итог валидации результата анализа.
enum ValidationOutcome {
  passed('passed'),
  failed('failed');

  const ValidationOutcome(this.wireValue);

  /// Значение итога валидации в контракте API.
  final String wireValue;

  /// Разбирает строку контракта; неизвестное значение — [failed].
  static ValidationOutcome fromWire(Object? value) {
    if (value is String) {
      for (final ValidationOutcome outcome in ValidationOutcome.values) {
        if (outcome.wireValue == value) {
          return outcome;
        }
      }
    }
    return ValidationOutcome.failed;
  }
}

/// Режим результата: одиночный или комбинированный.
enum AnalysisMode {
  single('single'),
  combined('combined');

  const AnalysisMode(this.wireValue);

  /// Значение режима в контракте API.
  final String wireValue;

  /// Разбирает строку контракта; неизвестное значение — [single].
  static AnalysisMode fromWire(Object? value) {
    if (value is String) {
      for (final AnalysisMode mode in AnalysisMode.values) {
        if (mode.wireValue == value) {
          return mode;
        }
      }
    }
    return AnalysisMode.single;
  }
}
