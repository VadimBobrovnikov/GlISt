import 'change_enums.dart';
import 'enums.dart';
import 'json_utils.dart';
import 'validation.dart';

/// Статистика по классу изменений (схема ChangeClassStats).
class ChangeClassStats {
  const ChangeClassStats({
    this.areaKm2 = 0,
    this.zonesCount = 0,
  });

  /// Суммарная площадь класса, км².
  final double areaKm2;

  /// Количество зон класса.
  final int zonesCount;

  /// Создаёт модель из JSON-объекта контракта.
  factory ChangeClassStats.fromJson(Map<String, dynamic> json) {
    return ChangeClassStats(
      areaKm2: JsonUtils.asDoubleOr(json['areaKm2'], 0),
      zonesCount: JsonUtils.asIntOr(json['zonesCount'], 0),
    );
  }
}

/// Результат по одной территории (схема ResultTerritory).
class ResultTerritory {
  const ResultTerritory({
    required this.position,
    required this.territoryName,
    required this.aoiType,
    required this.boundary,
    required this.stats,
    this.sourceRunId,
  });

  /// Позиция территории (1…5).
  final int position;

  /// Имя территории (из запроса либо «Область N»).
  final String territoryName;

  /// Тип территории.
  final AoiType aoiType;

  /// Граница территории в GeoJSON (EPSG:4326).
  final Map<String, dynamic> boundary;

  /// Статистика по классам изменений.
  final Map<ChangeClass, ChangeClassStats> stats;

  /// Запуск, в котором территория проанализирована.
  final String? sourceRunId;

  /// Создаёт модель из JSON-объекта контракта.
  factory ResultTerritory.fromJson(Map<String, dynamic> json) {
    final Map<String, dynamic> statsJson = JsonUtils.asMap(json['stats']);
    return ResultTerritory(
      position: JsonUtils.asIntOr(json['position'], 1),
      territoryName: JsonUtils.asStringOr(json['territoryName'], ''),
      aoiType: AoiType.fromWire(json['aoiType']),
      boundary: JsonUtils.asMap(json['boundary']),
      stats: <ChangeClass, ChangeClassStats>{
        ChangeClass.retained:
            ChangeClassStats.fromJson(JsonUtils.asMap(statsJson['retained'])),
        ChangeClass.lost:
            ChangeClassStats.fromJson(JsonUtils.asMap(statsJson['lost'])),
        ChangeClass.newZone:
            ChangeClassStats.fromJson(JsonUtils.asMap(statsJson['new'])),
      },
      sourceRunId: JsonUtils.asString(json['sourceRunId']),
    );
  }
}

/// Суммарная статистика по всем территориям (схема aggregate).
class AnalysisAggregate {
  const AnalysisAggregate({
    this.retained = const ChangeClassStats(),
    this.lost = const ChangeClassStats(),
    this.newZones = const ChangeClassStats(),
    this.note = '',
  });

  /// Статистика сохранившейся растительности.
  final ChangeClassStats retained;

  /// Статистика утраченной растительности.
  final ChangeClassStats lost;

  /// Статистика новой растительности.
  final ChangeClassStats newZones;

  /// Пометка о возможном пересечении территорий.
  final String note;

  /// Создаёт модель из JSON-объекта контракта.
  factory AnalysisAggregate.fromJson(Map<String, dynamic> json) {
    return AnalysisAggregate(
      retained: ChangeClassStats.fromJson(JsonUtils.asMap(json['retained'])),
      lost: ChangeClassStats.fromJson(JsonUtils.asMap(json['lost'])),
      newZones: ChangeClassStats.fromJson(JsonUtils.asMap(json['new'])),
      note: JsonUtils.asStringOr(json['note'], ''),
    );
  }
}

/// Источник данных и дата съёмки (схема AnalysisResult.sources).
class SourceRef {
  const SourceRef({
    required this.source,
    required this.collection,
    required this.sceneId,
    this.acquiredAt,
    this.cloudCover,
    this.resolutionM,
  });

  /// Источник (например, Sentinel-2).
  final String source;

  /// Коллекция снимков.
  final String collection;

  /// Идентификатор сцены.
  final String sceneId;

  /// Дата съёмки.
  final DateTime? acquiredAt;

  /// Облачность, %.
  final double? cloudCover;

  /// Пространственное разрешение, м.
  final double? resolutionM;

  /// Создаёт модель из JSON-объекта контракта.
  factory SourceRef.fromJson(Map<String, dynamic> json) {
    return SourceRef(
      source: JsonUtils.asStringOr(json['source'], ''),
      collection: JsonUtils.asStringOr(json['collection'], ''),
      sceneId: JsonUtils.asStringOr(json['sceneId'], ''),
      acquiredAt: JsonUtils.asDateTime(json['acquiredAt']),
      cloudCover: JsonUtils.asDouble(json['cloudCover']),
      resolutionM: JsonUtils.asDouble(json['resolutionM']),
    );
  }
}

/// Ссылка на фрагмент RAG-документа (схема citations, Принцип VII).
class Citation {
  const Citation({
    required this.document,
    required this.chunkOrdinal,
    this.docVersion,
    this.uri,
  });

  /// Название документа.
  final String document;

  /// Порядковый номер фрагмента.
  final int chunkOrdinal;

  /// Версия документа.
  final String? docVersion;

  /// Ссылка на документ.
  final String? uri;

  /// Создаёт модель из JSON-объекта контракта.
  factory Citation.fromJson(Map<String, dynamic> json) {
    return Citation(
      document: JsonUtils.asStringOr(json['document'], ''),
      chunkOrdinal: JsonUtils.asIntOr(json['chunkOrdinal'], 0),
      docVersion: JsonUtils.asString(json['docVersion']),
      uri: JsonUtils.asString(json['uri']),
    );
  }
}

/// Результат анализа (схема AnalysisResult из contracts/api.yaml).
class AnalysisResult {
  const AnalysisResult({
    required this.id,
    required this.runId,
    required this.mode,
    required this.territoriesCount,
    required this.validationOutcome,
    this.hasOverlap = false,
    this.crs = 'EPSG:4326',
    this.summaryText = '',
    this.explanationText = '',
    this.citations = const <Citation>[],
    this.sources = const <SourceRef>[],
    this.rejections = const <ValidationRejection>[],
    this.areasPurged = false,
    this.territories = const <ResultTerritory>[],
    this.aggregate = const AnalysisAggregate(),
    this.createdAt,
  });

  /// Идентификатор результата.
  final String id;

  /// Идентификатор запуска.
  final String runId;

  /// Режим результата.
  final AnalysisMode mode;

  /// Количество территорий (1…5).
  final int territoriesCount;

  /// Итог валидации.
  final ValidationOutcome validationOutcome;

  /// Признак пересечения территорий.
  final bool hasOverlap;

  /// Система координат (всегда EPSG:4326).
  final String crs;

  /// Краткое резюме результата.
  final String summaryText;

  /// Развёрнутое объяснение с привязкой к территориям.
  final String explanationText;

  /// Ссылки на фрагменты RAG.
  final List<Citation> citations;

  /// Источники данных.
  final List<SourceRef> sources;

  /// Отклонения результата валидатором.
  final List<ValidationRejection> rejections;

  /// Тяжёлые артефакты удалены по политике хранения (FR-022).
  final bool areasPurged;

  /// Данные по территориям.
  final List<ResultTerritory> territories;

  /// Суммарная статистика по территориям.
  final AnalysisAggregate aggregate;

  /// Время создания результата.
  final DateTime? createdAt;

  /// Создаёт модель из JSON-объекта контракта.
  factory AnalysisResult.fromJson(Map<String, dynamic> json) {
    return AnalysisResult(
      id: JsonUtils.asStringOr(json['id'], ''),
      runId: JsonUtils.asStringOr(json['runId'], ''),
      mode: AnalysisMode.fromWire(json['mode']),
      territoriesCount: JsonUtils.asIntOr(json['territoriesCount'], 0),
      validationOutcome: ValidationOutcome.fromWire(json['validationOutcome']),
      hasOverlap: JsonUtils.asBoolOr(json['hasOverlap'], false),
      crs: JsonUtils.asStringOr(json['crs'], 'EPSG:4326'),
      summaryText: JsonUtils.asStringOr(json['summaryText'], ''),
      explanationText: JsonUtils.asStringOr(json['explanationText'], ''),
      citations: JsonUtils.asMapList(json['citations'])
          .map(Citation.fromJson)
          .toList(growable: false),
      sources: JsonUtils.asMapList(json['sources'])
          .map(SourceRef.fromJson)
          .toList(growable: false),
      rejections: JsonUtils.asMapList(json['rejections'])
          .map(ValidationRejection.fromJson)
          .toList(growable: false),
      areasPurged: JsonUtils.asBoolOr(json['areasPurged'], false),
      territories: JsonUtils.asMapList(json['territories'])
          .map(ResultTerritory.fromJson)
          .toList(growable: false),
      aggregate: AnalysisAggregate.fromJson(JsonUtils.asMap(json['aggregate'])),
      createdAt: JsonUtils.asDateTime(json['createdAt']),
    );
  }
}
