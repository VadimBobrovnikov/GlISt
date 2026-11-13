/// Перечисления классов изменений, типов территорий и артефактов.
library;

/// Тип территории (схема ResultTerritory.aoiType).
enum AoiType {
  admin('admin'),
  poi('poi'),
  buffer('buffer');

  const AoiType(this.wireValue);

  /// Значение типа территории в контракте API.
  final String wireValue;

  /// Разбирает строку контракта; неизвестное значение — [admin].
  static AoiType fromWire(Object? value) {
    if (value is String) {
      for (final AoiType type in AoiType.values) {
        if (type.wireValue == value) {
          return type;
        }
      }
    }
    return AoiType.admin;
  }
}

/// Класс изменений растительности: сохранилось / утрачено / новое.
enum ChangeClass {
  retained('retained'),
  lost('lost'),
  newZone('new');

  const ChangeClass(this.wireValue);

  /// Значение класса в контракте API.
  final String wireValue;

  /// Разбирает строку контракта; неизвестное значение — `null`.
  static ChangeClass? fromWire(Object? value) {
    if (value is String) {
      for (final ChangeClass changeClass in ChangeClass.values) {
        if (changeClass.wireValue == value) {
          return changeClass;
        }
      }
    }
    return null;
  }
}

/// Вид артефакта (схема Artifact.kind).
enum ArtifactKind {
  previewPng('preview_png'),
  finalGeotiff('final_geotiff'),
  geojson('geojson'),
  pdf('pdf'),
  rawScene('raw_scene'),
  intermediateNdvi('intermediate_ndvi'),
  intermediateClassified('intermediate_classified'),
  unknown('unknown');

  const ArtifactKind(this.wireValue);

  /// Значение вида артефакта в контракте API.
  final String wireValue;

  /// Разбирает строку контракта; неизвестное значение — [unknown].
  static ArtifactKind fromWire(Object? value) {
    if (value is String) {
      for (final ArtifactKind kind in ArtifactKind.values) {
        if (kind.wireValue == value) {
          return kind;
        }
      }
    }
    return ArtifactKind.unknown;
  }
}

/// Формат экспорта результата.
enum ExportFormat {
  pdf('pdf'),
  geojson('geojson');

  const ExportFormat(this.wireValue);

  /// Значение формата в контракте API.
  final String wireValue;
}
