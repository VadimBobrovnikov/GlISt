/// Явные сценарии анализа GlISt.
///
/// Тип анализа выбирает пользователь (карточка сценария) либо он определяется
/// по свободному тексту и подтверждается до запуска. Сервер хранит и исполняет
/// именно это значение: подмены на анализ растительности быть не должно.
enum AnalysisType {
  vegetation('vegetation_change', 'Изменение растительности'),
  builtUp('built_up_change', 'Анализ застройки'),
  flood('flood_extent', 'Зоны затопления');

  const AnalysisType(this.wireValue, this.label);

  /// Значение типа анализа в контракте API.
  final String wireValue;

  /// Человекочитаемое название сценария.
  final String label;

  /// Разбирает строку контракта; неизвестное значение — `null`.
  static AnalysisType? tryFromWire(Object? value) {
    if (value is String) {
      for (final AnalysisType type in AnalysisType.values) {
        if (type.wireValue == value) {
          return type;
        }
      }
    }
    return null;
  }

  /// Разбирает строку контракта; неизвестное значение — [vegetation].
  static AnalysisType fromWire(Object? value) =>
      tryFromWire(value) ?? AnalysisType.vegetation;

  /// Короткие подписи классов для легенды карты и сводки.
  Map<String, String> get shortClassLabels {
    switch (this) {
      case AnalysisType.vegetation:
        return <String, String>{
          'retained': 'Сохранившиеся',
          'lost': 'Потерянные',
          'new': 'Новые зоны',
        };
      case AnalysisType.builtUp:
        return <String, String>{
          'retained': 'Застройка (обе даты)',
          'lost': 'Застройка исчезла',
          'new': 'Новая застройка',
        };
      case AnalysisType.flood:
        return <String, String>{
          'retained': 'Постоянная вода',
          'lost': 'Вода ушла',
          'new': 'Затопление',
        };
    }
  }

  /// Подписи классов зон по умолчанию (если сервер не прислал свои).
  Map<String, String> get defaultClassLabels {
    switch (this) {
      case AnalysisType.vegetation:
        return <String, String>{
          'retained': 'Растительность сохранилась',
          'lost': 'Растительность исчезла',
          'new': 'Появилась растительность',
        };
      case AnalysisType.builtUp:
        return <String, String>{
          'retained': 'Застроено на обеих датах',
          'lost': 'Застройка исчезла',
          'new': 'Появилась застройка',
        };
      case AnalysisType.flood:
        return <String, String>{
          'retained': 'Постоянная вода',
          'lost': 'Вода была в базовый период, на дату события нет',
          'new': 'Временное затопление',
        };
    }
  }
}
