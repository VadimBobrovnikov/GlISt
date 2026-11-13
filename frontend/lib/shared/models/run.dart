import 'enums.dart';
import 'json_utils.dart';
import 'validation.dart';

/// Версии моделей и промптов, использованных в запуске (FR-018).
class ModelVersions {
  const ModelVersions({
    required this.llmModel,
    required this.embeddingModel,
    required this.promptVersions,
  });

  /// Модель LLM.
  final String llmModel;

  /// Модель эмбеддингов.
  final String embeddingModel;

  /// Версии промптов по узлам графа.
  final List<String> promptVersions;

  /// Создаёт модель из JSON-объекта контракта.
  factory ModelVersions.fromJson(Map<String, dynamic> json) {
    return ModelVersions(
      llmModel: JsonUtils.asStringOr(json['llmModel'], ''),
      embeddingModel: JsonUtils.asStringOr(json['embeddingModel'], ''),
      promptVersions: JsonUtils.asStringList(json['promptVersions']),
    );
  }
}

/// Этап обработки запуска (схема RunStage).
class RunStage {
  const RunStage({
    required this.seq,
    required this.attemptNo,
    required this.agent,
    required this.stageCode,
    required this.title,
    required this.description,
    required this.status,
    this.startedAt,
    this.finishedAt,
    this.details = const <String, dynamic>{},
  });

  /// Порядковый номер этапа.
  final int seq;

  /// Номер попытки (0 или 1).
  final int attemptNo;

  /// Имя агента: researcher / analyst / visualizer / validator.
  final String agent;

  /// Код этапа: data_search, composite, ndvi, landcover, map_layers, validation…
  final String stageCode;

  /// Заголовок этапа.
  final String title;

  /// Краткое описание работы агента (FR-010, US4).
  final String description;

  /// Статус этапа: running / completed / failed / skipped.
  final String status;

  /// Время начала этапа.
  final DateTime? startedAt;

  /// Время завершения этапа.
  final DateTime? finishedAt;

  /// Дополнительные детали этапа.
  final Map<String, dynamic> details;

  /// Создаёт модель из JSON-объекта контракта.
  factory RunStage.fromJson(Map<String, dynamic> json) {
    return RunStage(
      seq: JsonUtils.asIntOr(json['seq'], 0),
      attemptNo: JsonUtils.asIntOr(json['attemptNo'], 0),
      agent: JsonUtils.asStringOr(json['agent'], ''),
      stageCode: JsonUtils.asStringOr(json['stageCode'], ''),
      title: JsonUtils.asStringOr(json['title'], ''),
      description: JsonUtils.asStringOr(json['description'], ''),
      status: JsonUtils.asStringOr(json['status'], ''),
      startedAt: JsonUtils.asDateTime(json['startedAt']),
      finishedAt: JsonUtils.asDateTime(json['finishedAt']),
      details: JsonUtils.asMap(json['details']),
    );
  }
}

/// Краткая информация о запуске (используется в QueryCreated).
class RunSummary {
  const RunSummary({
    required this.id,
    required this.status,
    this.createdAt,
  });

  /// Идентификатор запуска.
  final String id;

  /// Текущий статус запуска.
  final RunStatus status;

  /// Время создания запуска.
  final DateTime? createdAt;

  /// Создаёт модель из JSON-объекта контракта.
  factory RunSummary.fromJson(Map<String, dynamic> json) {
    return RunSummary(
      id: JsonUtils.asStringOr(json['id'], ''),
      status: RunStatus.fromWire(json['status']),
      createdAt: JsonUtils.asDateTime(json['createdAt']),
    );
  }
}

/// Запуск анализа (схема Run из contracts/api.yaml).
class Run {
  const Run({
    required this.id,
    required this.queryId,
    required this.status,
    required this.attemptCount,
    this.createdAt,
    this.parentRunId,
    this.reusedResultId,
    this.statusReason,
    this.queuePosition,
    this.queuedAt,
    this.startedAt,
    this.finishedAt,
    this.rejectionCategory,
    this.traceId,
    this.seed,
    this.settingsHash,
    this.paramsHash,
    this.ragIndexVersion,
    this.modelVersions,
    this.isReference = false,
    this.isPinned = false,
    this.retentionUntil,
    this.purgeNoticeAt,
    this.stages = const <RunStage>[],
    this.rejections = const <ValidationRejection>[],
  });

  /// Идентификатор запуска.
  final String id;

  /// Идентификатор запроса.
  final String queryId;

  /// Текущий статус запуска.
  final RunStatus status;

  /// Число переработок (0 или 1) — FR-007.
  final int attemptCount;

  /// Время создания запуска.
  final DateTime? createdAt;

  /// Родительский запуск (для уточнений).
  final String? parentRunId;

  /// Ссылка на переиспользованный результат.
  final String? reusedResultId;

  /// Причина терминального статуса.
  final String? statusReason;

  /// Позиция в очереди.
  final int? queuePosition;

  /// Время постановки в очередь.
  final DateTime? queuedAt;

  /// Время начала обработки.
  final DateTime? startedAt;

  /// Время завершения.
  final DateTime? finishedAt;

  /// Категория отклонения валидатором.
  final RejectionCategory? rejectionCategory;

  /// Идентификатор трассы LangFuse.
  final String? traceId;

  /// Seed воспроизводимости.
  final int? seed;

  /// Хеш настроек.
  final String? settingsHash;

  /// Хеш параметров запроса.
  final String? paramsHash;

  /// Версия индекса RAG.
  final String? ragIndexVersion;

  /// Версии моделей и промптов.
  final ModelVersions? modelVersions;

  /// Признак эталонного запуска.
  final bool isReference;

  /// Признак «сохранить навсегда».
  final bool isPinned;

  /// Срок хранения результата.
  final DateTime? retentionUntil;

  /// Время уведомления о предстоящей очистке.
  final DateTime? purgeNoticeAt;

  /// Этапы обработки.
  final List<RunStage> stages;

  /// Отклонения результата валидатором.
  final List<ValidationRejection> rejections;

  /// Создаёт модель из JSON-объекта контракта.
  factory Run.fromJson(Map<String, dynamic> json) {
    final Map<String, dynamic>? versions = json['modelVersions'] == null
        ? null
        : JsonUtils.asMap(json['modelVersions']);
    return Run(
      id: JsonUtils.asStringOr(json['id'], ''),
      queryId: JsonUtils.asStringOr(json['queryId'], ''),
      status: RunStatus.fromWire(json['status']),
      attemptCount: JsonUtils.asIntOr(json['attemptCount'], 0),
      createdAt: JsonUtils.asDateTime(json['createdAt']),
      parentRunId: JsonUtils.asString(json['parentRunId']),
      reusedResultId: JsonUtils.asString(json['reusedResultId']),
      statusReason: JsonUtils.asString(json['statusReason']),
      queuePosition: JsonUtils.asInt(json['queuePosition']),
      queuedAt: JsonUtils.asDateTime(json['queuedAt']),
      startedAt: JsonUtils.asDateTime(json['startedAt']),
      finishedAt: JsonUtils.asDateTime(json['finishedAt']),
      rejectionCategory: RejectionCategory.fromWire(json['rejectionCategory']),
      traceId: JsonUtils.asString(json['traceId']),
      seed: JsonUtils.asInt(json['seed']),
      settingsHash: JsonUtils.asString(json['settingsHash']),
      paramsHash: JsonUtils.asString(json['paramsHash']),
      ragIndexVersion: JsonUtils.asString(json['ragIndexVersion']),
      modelVersions:
          versions == null ? null : ModelVersions.fromJson(versions),
      isReference: JsonUtils.asBoolOr(json['isReference'], false),
      isPinned: JsonUtils.asBoolOr(json['isPinned'], false),
      retentionUntil: JsonUtils.asDateTime(json['retentionUntil']),
      purgeNoticeAt: JsonUtils.asDateTime(json['purgeNoticeAt']),
      stages: JsonUtils.asMapList(json['stages'])
          .map(RunStage.fromJson)
          .toList(growable: false),
      rejections: JsonUtils.asMapList(json['rejections'])
          .map(ValidationRejection.fromJson)
          .toList(growable: false),
    );
  }
}
