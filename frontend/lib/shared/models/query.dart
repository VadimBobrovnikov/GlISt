import 'analysis_type.dart';
import 'enums.dart';
import 'json_utils.dart';
import 'run.dart';

/// Краткая информация о запросе (схема QuerySummary).
class QuerySummary {
  const QuerySummary({
    required this.id,
    required this.text,
    required this.language,
    required this.queryKind,
    required this.status,
    this.parentQueryId,
    this.lastRunId,
    this.queuePosition,
    this.purgeNoticeAt,
    this.isPinned = false,
    this.createdAt,
    this.analysisType = AnalysisType.vegetation,
  });

  /// Идентификатор запроса.
  final String id;

  /// Текст запроса на естественном языке.
  final String text;

  /// Язык запроса.
  final QueryLanguage language;

  /// Вид запроса (первичный / уточняющий).
  final QueryKind queryKind;

  /// Текущий статус запроса.
  final RunStatus status;

  /// Идентификатор родительского запроса.
  final String? parentQueryId;

  /// Идентификатор последнего запуска.
  final String? lastRunId;

  /// Позиция в очереди.
  final int? queuePosition;

  /// Время уведомления о предстоящей очистке.
  final DateTime? purgeNoticeAt;

  /// Признак «сохранить навсегда».
  final bool isPinned;

  /// Время создания запроса.
  final DateTime? createdAt;

  /// Сценарий анализа, выбранный при создании запроса.
  final AnalysisType analysisType;

  /// Создаёт модель из JSON-объекта контракта.
  factory QuerySummary.fromJson(Map<String, dynamic> json) {
    return QuerySummary(
      id: JsonUtils.asStringOr(json['id'], ''),
      text: JsonUtils.asStringOr(json['text'], ''),
      language: QueryLanguage.fromWire(json['language']),
      queryKind: QueryKind.fromWire(json['queryKind']),
      status: RunStatus.fromWire(json['status']),
      parentQueryId: JsonUtils.asString(json['parentQueryId']),
      lastRunId: JsonUtils.asString(json['lastRunId']),
      queuePosition: JsonUtils.asInt(json['queuePosition']),
      purgeNoticeAt: JsonUtils.asDateTime(json['purgeNoticeAt']),
      isPinned: JsonUtils.asBoolOr(json['isPinned'], false),
      createdAt: JsonUtils.asDateTime(json['createdAt']),
      analysisType: AnalysisType.fromWire(json['analysisType']),
    );
  }
}

/// Вариант ответа на уточняющий вопрос.
class ClarificationOption {
  const ClarificationOption({
    required this.id,
    required this.label,
    this.description,
  });

  /// Идентификатор варианта.
  final String id;

  /// Подпись варианта.
  final String label;

  /// Пояснение к варианту.
  final String? description;

  /// Создаёт модель из JSON-объекта контракта.
  factory ClarificationOption.fromJson(Map<String, dynamic> json) {
    return ClarificationOption(
      id: JsonUtils.asStringOr(json['id'], ''),
      label: JsonUtils.asStringOr(json['label'], ''),
      description: JsonUtils.asString(json['description']),
    );
  }
}

/// Уточняющий вопрос (схема ClarificationRequest).
class ClarificationRequest {
  const ClarificationRequest({
    required this.id,
    required this.queryId,
    required this.kind,
    required this.questionText,
    required this.status,
    this.options = const <ClarificationOption>[],
  });

  /// Идентификатор уточнения.
  final String id;

  /// Идентификатор запроса.
  final String queryId;

  /// Тип уточнения.
  final ClarificationKind kind;

  /// Текст вопроса.
  final String questionText;

  /// Статус уточнения (ожидает ответа / отвечен / истёк).
  final String status;

  /// Варианты ответа.
  final List<ClarificationOption> options;

  /// Создаёт модель из JSON-объекта контракта.
  factory ClarificationRequest.fromJson(Map<String, dynamic> json) {
    return ClarificationRequest(
      id: JsonUtils.asStringOr(json['id'], ''),
      queryId: JsonUtils.asStringOr(json['queryId'], ''),
      kind: ClarificationKind.fromWire(json['kind']),
      questionText: JsonUtils.asStringOr(json['questionText'], ''),
      status: JsonUtils.asStringOr(json['status'], ''),
      options: JsonUtils.asMapList(json['options'])
          .map(ClarificationOption.fromJson)
          .toList(growable: false),
    );
  }
}

/// Ответ POST /api/v1/queries (схема QueryCreated).
class QueryCreated {
  const QueryCreated({
    required this.query,
    required this.run,
    this.clarification,
  });

  /// Созданный запрос.
  final QuerySummary query;

  /// Созданный запуск анализа.
  final Run run;

  /// Уточнение, если требуется до начала анализа.
  final ClarificationRequest? clarification;

  /// Создаёт модель из JSON-объекта контракта.
  factory QueryCreated.fromJson(Map<String, dynamic> json) {
    final Object? clarification = json['clarification'];
    return QueryCreated(
      query: QuerySummary.fromJson(JsonUtils.asMap(json['query'])),
      run: Run.fromJson(JsonUtils.asMap(json['run'])),
      clarification: clarification == null
          ? null
          : ClarificationRequest.fromJson(JsonUtils.asMap(clarification)),
    );
  }
}

/// Страница истории запросов (схема QueryPage).
class QueryPage {
  const QueryPage({
    this.items = const <QuerySummary>[],
    this.nextCursor,
  });

  /// Запросы текущей страницы.
  final List<QuerySummary> items;

  /// Курсор следующей страницы.
  final String? nextCursor;

  /// Создаёт модель из JSON-объекта контракта.
  factory QueryPage.fromJson(Map<String, dynamic> json) {
    return QueryPage(
      items: JsonUtils.asMapList(json['items'])
          .map(QuerySummary.fromJson)
          .toList(growable: false),
      nextCursor: JsonUtils.asString(json['nextCursor']),
    );
  }
}
