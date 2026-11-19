import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_map/flutter_map.dart';
import 'package:latlong2/latlong.dart';

import '../../core/api_client.dart';
import '../../core/api_provider.dart';
import '../../core/app_theme.dart';
import '../../shared/models/models.dart';

/// Зона изменений, извлечённая из GeoJSON.
class ChangeZone {
  const ChangeZone({required this.changeClass, required this.rings});

  /// Класс изменений (retained / lost / new).
  final ChangeClass changeClass;

  /// Кольца полигонов (внешние контуры), координаты WGS84.
  final List<List<LatLng>> rings;
}

/// Цвета слоёв карты из дизайн-системы.
///
/// Оттенки зависят от сценария: для затопления синий — постоянная вода,
/// голубой — временное затопление; для застройки серый/оранжевый. Легенда и
/// полигоны берут цвет из одного места, чтобы они не расходились.
class MapColors {
  const MapColors._();

  /// Цвет контура зоны.
  static Color stroke(ChangeClass changeClass, [AnalysisType? analysisType]) {
    switch (analysisType) {
      case AnalysisType.flood:
        return switch (changeClass) {
          ChangeClass.retained => AppColors.waterPermanent,
          ChangeClass.lost => AppColors.waterReceded,
          ChangeClass.newZone => AppColors.waterTemporary,
        };
      case AnalysisType.builtUp:
        return switch (changeClass) {
          ChangeClass.retained => AppColors.builtRetained,
          ChangeClass.lost => AppColors.builtLost,
          ChangeClass.newZone => AppColors.builtNew,
        };
      case AnalysisType.vegetation:
      case null:
        return switch (changeClass) {
          ChangeClass.retained => AppColors.retained,
          ChangeClass.lost => AppColors.lost,
          ChangeClass.newZone => AppColors.newZone,
        };
    }
  }

  /// Цвет заливки зоны (40% прозрачности).
  static Color fill(ChangeClass changeClass, [AnalysisType? analysisType]) =>
      stroke(changeClass, analysisType).withValues(alpha: 0.4);
}

/// Парсит FeatureCollection зон изменений из строки GeoJSON.
///
/// Класс зоны берётся из `properties.change_class` (или `changeClass`).
/// Некорректный JSON или отсутствие features дают пустой список.
List<ChangeZone> parseChangeZones(String raw) {
  final Object? decoded = _decodeOrNull(raw);
  final Map<String, dynamic> root = JsonUtils.asMap(decoded);
  final List<Map<String, dynamic>> features =
      JsonUtils.asMapList(root['features']);
  final List<ChangeZone> zones = <ChangeZone>[];
  for (final Map<String, dynamic> feature in features) {
    final Map<String, dynamic> properties =
        JsonUtils.asMap(feature['properties']);
    final ChangeClass? changeClass = ChangeClass.fromWire(
      properties['change_class'] ?? properties['changeClass'],
    );
    if (changeClass == null) {
      continue;
    }
    final List<List<LatLng>> rings =
        _ringsFromGeometry(JsonUtils.asMap(feature['geometry']));
    if (rings.isNotEmpty) {
      zones.add(ChangeZone(changeClass: changeClass, rings: rings));
    }
  }
  return zones;
}

Object? _decodeOrNull(String raw) {
  try {
    return jsonDecode(raw);
  } on FormatException {
    return null;
  }
}

List<List<LatLng>> _ringsFromGeometry(Map<String, dynamic> geometry) {
  final String type = JsonUtils.asStringOr(geometry['type'], '');
  final Object? coordinates = geometry['coordinates'];
  final List<List<LatLng>> rings = <List<LatLng>>[];
  if (type == 'Polygon') {
    rings.addAll(_ringsFromPolygon(coordinates));
  } else if (type == 'MultiPolygon') {
    for (final Object? polygon in JsonUtils.asList(coordinates)) {
      rings.addAll(_ringsFromPolygon(polygon));
    }
  }
  return rings;
}

List<List<LatLng>> _ringsFromPolygon(Object? coordinates) {
  final List<List<LatLng>> rings = <List<LatLng>>[];
  for (final Object? ringRaw in JsonUtils.asList(coordinates)) {
    final List<LatLng> ring = <LatLng>[];
    for (final Object? pointRaw in JsonUtils.asList(ringRaw)) {
      final List<dynamic> point = JsonUtils.asList(pointRaw);
      if (point.length >= 2) {
        final double? lng = JsonUtils.asDouble(point[0]);
        final double? lat = JsonUtils.asDouble(point[1]);
        if (lat != null && lng != null) {
          ring.add(LatLng(lat, lng));
        }
      }
    }
    if (ring.length >= 3) {
      rings.add(ring);
    }
  }
  return rings;
}

/// Интерактивная карта результата анализа на OSM-тайлах (T068).
///
/// Рисует зоны изменений из GeoJSON (retained / lost / new). Если готовая
/// строка GeoJSON не передана, заливает границы территорий цветом
/// доминирующего класса изменений.
class MapView extends StatefulWidget {
  const MapView({
    required this.result,
    this.changeZonesGeoJson,
    super.key,
  });

  /// Результат анализа с территориями и статистикой.
  final AnalysisResult result;

  /// Необязательный FeatureCollection зон изменений (строка GeoJSON).
  final String? changeZonesGeoJson;

  @override
  State<MapView> createState() => _MapViewState();
}

class _MapViewState extends State<MapView> {
  final MapController _controller = MapController();
  final Set<ChangeClass> _visible = <ChangeClass>{
    ChangeClass.retained,
    ChangeClass.lost,
    ChangeClass.newZone,
  };
  ApiClient? _client;
  bool _showPolygons = true;
  bool _showPreview = true;
  bool _showUnknown = false;
  String _period = 'end';
  double _previewOpacity = 0.65;
  Map<String, dynamic>? _sample;
  String? _sampleError;
  bool _sampling = false;

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    _client ??= ApiScope.of(context);
  }

  LatLngBounds? get _previewBounds {
    final List<double>? bbox = widget.result.bbox;
    if (bbox == null || bbox.length != 4) {
      return null;
    }
    return LatLngBounds(
      LatLng(bbox[1], bbox[0]),
      LatLng(bbox[3], bbox[2]),
    );
  }

  bool _hasScenes(String period) {
    final Object? scenes = widget.result.diagnostics['scenes'];
    if (scenes is! Map<String, dynamic>) {
      return false;
    }
    final Object? items = scenes[period];
    return items is List<dynamic> && items.isNotEmpty;
  }

  double? get _sharedCoverage {
    final Object? value = widget.result.coverage['sharedValid'];
    return value is num ? value.toDouble() : null;
  }

  double? get _unknownCoverage {
    final Object? value = widget.result.coverage['unknown'];
    return value is num ? value.toDouble() : null;
  }

  double? _coverageArea(String key) {
    final Object? value = widget.result.coverage[key];
    return value is num ? value.toDouble() : null;
  }

  String _formatCoverageArea(double? areaKm2) {
    if (areaKm2 == null) {
      return '';
    }
    if (areaKm2.abs() < 1) {
      return ' · ${areaKm2.toStringAsFixed(2)} км²';
    }
    return ' · ${areaKm2.toStringAsFixed(1)} км²';
  }

  Future<void> _sampleAt(LatLng point) async {
    final ApiClient? client = _client;
    if (client == null) {
      return;
    }
    setState(() {
      _sampling = true;
      _sampleError = null;
    });
    try {
      final Map<String, dynamic> data = await client.sampleRun(
        widget.result.runId,
        point.latitude,
        point.longitude,
      );
      if (!mounted) {
        return;
      }
      setState(() {
        _sample = data;
      });
    } on ApiException catch (error) {
      if (!mounted) {
        return;
      }
      setState(() {
        _sampleError = error.message;
        _sample = null;
      });
    } finally {
      if (mounted) {
        setState(() {
          _sampling = false;
        });
      }
    }
  }

  List<ChangeZone> get _zones {
    final String? raw = widget.changeZonesGeoJson;
    if (raw != null && raw.isNotEmpty) {
      final List<ChangeZone> parsed = parseChangeZones(raw);
      if (parsed.isNotEmpty) {
        return parsed;
      }
    }
    return _zonesFromTerritories(widget.result.territories);
  }

  List<ChangeZone> _zonesFromTerritories(List<ResultTerritory> territories) {
    final List<ChangeZone> zones = <ChangeZone>[];
    for (final ResultTerritory territory in territories) {
      final List<List<LatLng>> rings = _ringsFromGeometry(territory.boundary);
      if (rings.isNotEmpty) {
        zones.add(
          ChangeZone(changeClass: _dominantClass(territory), rings: rings),
        );
      }
    }
    return zones;
  }

  /// Контур запрошенной территории (AOI) — рисуется всегда.
  List<Polygon> _aoiPolygons() {
    final List<Polygon> polygons = <Polygon>[];
    for (final ResultTerritory territory in widget.result.territories) {
      for (final List<LatLng> ring in _ringsFromGeometry(territory.boundary)) {
        polygons.add(
          Polygon(
            points: ring,
            color: Colors.transparent,
            borderColor: AppColors.primary,
            borderStrokeWidth: 2,
          ),
        );
      }
    }
    return polygons;
  }

  ChangeClass _dominantClass(ResultTerritory territory) {
    final double retained = territory.stats[ChangeClass.retained]?.areaKm2 ?? 0;
    final double lost = territory.stats[ChangeClass.lost]?.areaKm2 ?? 0;
    final double newZones = territory.stats[ChangeClass.newZone]?.areaKm2 ?? 0;
    if (lost > 0 && lost >= retained && lost >= newZones) {
      return ChangeClass.lost;
    }
    if (newZones > 0 && newZones >= retained) {
      return ChangeClass.newZone;
    }
    return ChangeClass.retained;
  }

  void _toggle(ChangeClass changeClass) {
    setState(() {
      if (_visible.contains(changeClass)) {
        _visible.remove(changeClass);
      } else {
        _visible.add(changeClass);
      }
    });
  }

  void _zoomBy(double delta) {
    final double target =
        (_controller.camera.zoom + delta).clamp(3.0, 18.0).toDouble();
    _controller.move(_controller.camera.center, target);
  }

  @override
  Widget build(BuildContext context) {
    final List<ChangeZone> zones = _zones
        .where((ChangeZone zone) => _visible.contains(zone.changeClass))
        .toList(growable: false);
    final List<Polygon> aoiPolygons = _aoiPolygons();
    final List<LatLng> allPoints = <LatLng>[];
    for (final Polygon polygon in aoiPolygons) {
      allPoints.addAll(polygon.points);
    }
    final List<Polygon> polygons = <Polygon>[];
    for (final ChangeZone zone in zones) {
      for (final List<LatLng> ring in zone.rings) {
        allPoints.addAll(ring);
        polygons.add(
          Polygon(
            points: ring,
            color: MapColors.fill(zone.changeClass, widget.result.analysisType),
            borderColor: MapColors.stroke(
              zone.changeClass,
              widget.result.analysisType,
            ),
            borderStrokeWidth: 2,
          ),
        );
      }
    }
    final MapOptions options;
    if (allPoints.length >= 2) {
      options = MapOptions(
        initialCameraFit: CameraFit.bounds(
          bounds: LatLngBounds.fromPoints(allPoints),
          padding: const EdgeInsets.all(48),
        ),
        minZoom: 3,
        maxZoom: 18,
        onTap: (TapPosition position, LatLng point) {
          unawaited(_sampleAt(point));
        },
      );
    } else {
      options = MapOptions(
        initialCenter: const LatLng(55.751244, 37.618423),
        initialZoom: 10,
        minZoom: 3,
        maxZoom: 18,
        onTap: (TapPosition position, LatLng point) {
          unawaited(_sampleAt(point));
        },
      );
    }
    final LatLngBounds? previewBounds = _previewBounds;
    final bool previewAvailable = previewBounds != null &&
        _client != null &&
        _hasScenes(_period);
    return Stack(
      children: <Widget>[
        FlutterMap(
          mapController: _controller,
          options: options,
          children: <Widget>[
            TileLayer(
              urlTemplate: 'https://tile.openstreetmap.org/{z}/{x}/{y}.png',
              userAgentPackageName: 'ru.glist.app',
              maxZoom: 19,
            ),
            if (previewAvailable && _showPreview)
              OverlayImageLayer(
                overlayImages: <OverlayImage>[
                  OverlayImage(
                    bounds: previewBounds,
                    imageProvider: NetworkImage(
                      _client!.previewUrl(widget.result.runId, _period),
                    ),
                    opacity: _previewOpacity,
                  ),
                ],
              ),
            if (previewBounds != null &&
                _client != null &&
                _showUnknown &&
                _hasScenes('start') &&
                _hasScenes('end'))
              OverlayImageLayer(
                overlayImages: <OverlayImage>[
                  OverlayImage(
                    bounds: previewBounds,
                    imageProvider: NetworkImage(
                      _client!.previewUrl(widget.result.runId, 'unknown'),
                    ),
                    opacity: _previewOpacity,
                  ),
                ],
              ),
            if (_showPolygons) PolygonLayer(polygons: polygons),
            if (aoiPolygons.isNotEmpty) PolygonLayer(polygons: aoiPolygons),
          ],
        ),
        Positioned(top: 16, left: 16, child: _analysisChip()),
        Positioned(top: 16, right: 16, child: _zoomControls()),
        Positioned(top: 64, right: 16, child: _dataControls()),
        Positioned(left: 16, bottom: 16, child: _legend()),
        Positioned(right: 16, bottom: 16, child: _statusBadge()),
        if (_sample != null || _sampleError != null || _sampling)
          Positioned(left: 16, bottom: 170, child: _sampleCard()),
      ],
    );
  }

  Widget _analysisChip() {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
      decoration: BoxDecoration(
        color: AppColors.surface,
        borderRadius: BorderRadius.circular(10),
        boxShadow: <BoxShadow>[
          BoxShadow(
            color: Colors.black.withValues(alpha: 0.08),
            blurRadius: 10,
            offset: const Offset(0, 3),
          ),
        ],
      ),
      child: const Row(
        mainAxisSize: MainAxisSize.min,
        children: <Widget>[
          Icon(Icons.layers_outlined, size: 16, color: AppColors.primary),
          SizedBox(width: 6),
          Text(
            'Анализ растительности',
            style: TextStyle(
              fontSize: 12,
              fontWeight: FontWeight.w600,
              color: AppColors.text,
            ),
          ),
        ],
      ),
    );
  }

  Widget _zoomControls() {
    return Column(
      children: <Widget>[
        _zoomButton(Icons.add, () => _zoomBy(1)),
        const SizedBox(height: 6),
        _zoomButton(Icons.remove, () => _zoomBy(-1)),
      ],
    );
  }

  Widget _zoomButton(IconData icon, VoidCallback onTap) {
    return Material(
      color: AppColors.surface,
      borderRadius: BorderRadius.circular(8),
      elevation: 2,
      child: InkWell(
        onTap: onTap,
        borderRadius: BorderRadius.circular(8),
        child: SizedBox(
          width: 36,
          height: 36,
          child: Icon(icon, size: 18, color: AppColors.text),
        ),
      ),
    );
  }

  Widget _dataControls() {
    final Object? datesValue = widget.result.diagnostics['actualDates'];
    final Map<String, dynamic> dates = datesValue is Map<String, dynamic>
        ? datesValue
        : <String, dynamic>{};
    final String before = dates['start'] is List<dynamic>
        ? (dates['start'] as List<dynamic>).whereType<String>().join(', ')
        : '';
    final String after = dates['end'] is List<dynamic>
        ? (dates['end'] as List<dynamic>).whereType<String>().join(', ')
        : '';
    final bool hasStart = _hasScenes('start');
    final bool hasEnd = _hasScenes('end');
    return Container(
      width: 230,
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: AppColors.surface,
        borderRadius: BorderRadius.circular(12),
        boxShadow: <BoxShadow>[
          BoxShadow(
            color: Colors.black.withValues(alpha: 0.08),
            blurRadius: 10,
            offset: const Offset(0, 3),
          ),
        ],
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisSize: MainAxisSize.min,
        children: <Widget>[
          const Text(
            'Данные расчёта',
            style: TextStyle(
              fontSize: 12,
              fontWeight: FontWeight.w700,
              color: AppColors.text,
            ),
          ),
          const SizedBox(height: 2),
          const Text(
            'Снимки сцен расчёта, не подложка OSM',
            style: TextStyle(fontSize: 10, color: AppColors.muted),
          ),
          const SizedBox(height: 8),
          Row(
            children: <Widget>[
              ChoiceChip(
                label: const Text('До', style: TextStyle(fontSize: 11)),
                selected: _period == 'start',
                onSelected: hasStart
                    ? (_) => setState(() => _period = 'start')
                    : null,
              ),
              const SizedBox(width: 6),
              ChoiceChip(
                label: const Text('После', style: TextStyle(fontSize: 11)),
                selected: _period == 'end',
                onSelected: hasEnd
                    ? (_) => setState(() => _period = 'end')
                    : null,
              ),
            ],
          ),
          _switchRow(
            'Полигоны',
            _showPolygons,
            (bool value) => setState(() => _showPolygons = value),
          ),
          _switchRow(
            'Снимок',
            _showPreview,
            (bool value) => setState(() => _showPreview = value),
          ),
          _switchRow(
            'Неизвестно (облака/NoData)',
            _showUnknown,
            (bool value) => setState(() => _showUnknown = value),
          ),
          if (_sharedCoverage != null)
            Text(
              'Валидно на обеих датах: '
              '${(_sharedCoverage! * 100).toStringAsFixed(1)}%'
              '${_formatCoverageArea(_coverageArea('sharedValidAreaKm2'))}',
              style: const TextStyle(fontSize: 10, color: AppColors.muted),
            ),
          if (_unknownCoverage != null)
            Text(
              'Неизвестно: ${(_unknownCoverage! * 100).toStringAsFixed(1)}%'
              '${_formatCoverageArea(_coverageArea('unknownAreaKm2'))}',
              style: const TextStyle(fontSize: 10, color: AppColors.muted),
            ),
          Row(
            children: <Widget>[
              const Text(
                'Прозрачность',
                style: TextStyle(fontSize: 11, color: AppColors.text),
              ),
              Expanded(
                child: Slider(
                  value: _previewOpacity,
                  min: 0.1,
                  max: 1,
                  onChanged: (double value) =>
                      setState(() => _previewOpacity = value),
                ),
              ),
            ],
          ),
          if (before.isNotEmpty || after.isNotEmpty)
            Text(
              'Даты: до ${before.isEmpty ? '—' : before}; '
              'после ${after.isEmpty ? '—' : after}.',
              style: const TextStyle(fontSize: 10, color: AppColors.muted),
            ),
          const Text(
            'Карта: крупнейшие зоны; статистика — по всем полигонам.',
            style: TextStyle(fontSize: 10, color: AppColors.muted),
          ),
        ],
      ),
    );
  }

  Widget _switchRow(
    String label,
    bool value,
    ValueChanged<bool> onChanged,
  ) {
    return SizedBox(
      height: 32,
      child: Row(
        mainAxisAlignment: MainAxisAlignment.spaceBetween,
        children: <Widget>[
          Text(
            label,
            style: const TextStyle(fontSize: 11, color: AppColors.text),
          ),
          Switch(
            value: value,
            onChanged: onChanged,
            materialTapTargetSize: MaterialTapTargetSize.shrinkWrap,
          ),
        ],
      ),
    );
  }

  Widget _sampleCard() {
    final Map<String, dynamic>? data = _sample;
    final String? error = _sampleError;
    return Container(
      width: 260,
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: AppColors.surface,
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: AppColors.border),
        boxShadow: <BoxShadow>[
          BoxShadow(
            color: Colors.black.withValues(alpha: 0.1),
            blurRadius: 12,
            offset: const Offset(0, 4),
          ),
        ],
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisSize: MainAxisSize.min,
        children: <Widget>[
          Row(
            children: <Widget>[
              const Expanded(
                child: Text(
                  'Значения в точке',
                  style: TextStyle(
                    fontSize: 12,
                    fontWeight: FontWeight.w700,
                    color: AppColors.text,
                  ),
                ),
              ),
              InkWell(
                onTap: () => setState(() {
                  _sample = null;
                  _sampleError = null;
                }),
                child: const Icon(
                  Icons.close,
                  size: 16,
                  color: AppColors.muted,
                ),
              ),
            ],
          ),
          const SizedBox(height: 6),
          if (_sampling) const LinearProgressIndicator(minHeight: 2),
          if (error != null)
            Text(
              error,
              style: const TextStyle(fontSize: 11, color: AppColors.lost),
            ),
          if (data != null) ...<Widget>[
            Text(
              'Класс: ${_transitionLabel(data['transition'])}',
              style: const TextStyle(fontSize: 12, color: AppColors.text),
            ),
            const SizedBox(height: 2),
            Text(
              'NDVI до: ${_formatNdvi(data['ndviBefore'])}; '
              'после: ${_formatNdvi(data['ndviAfter'])}',
              style: const TextStyle(fontSize: 11, color: AppColors.text),
            ),
            Text(
              'Порог NDVI: ${_formatNdvi(data['ndviThreshold'])}',
              style: const TextStyle(fontSize: 11, color: AppColors.muted),
            ),
          ],
        ],
      ),
    );
  }

  String _formatNdvi(Object? value) =>
      value is num ? value.toStringAsFixed(3) : '—';

  String _transitionLabel(Object? value) {
    if (value is String && value.isNotEmpty) {
      final String? label = widget.result.classLabels[value];
      if (label != null && label.isNotEmpty) {
        return label;
      }
    }
    return switch (value) {
      'retained' => 'сохранено (не изменение)',
      'lost' => 'потеряно',
      'new' => 'новые зоны',
      'stable_non_vegetation' => 'без растительности',
      _ => 'недостаточно данных',
    };
  }

  Widget _legend() {
    return Container(
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: AppColors.surface,
        borderRadius: BorderRadius.circular(12),
        boxShadow: <BoxShadow>[
          BoxShadow(
            color: Colors.black.withValues(alpha: 0.08),
            blurRadius: 10,
            offset: const Offset(0, 3),
          ),
        ],
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisSize: MainAxisSize.min,
        children: <Widget>[
          const Text(
            'Слои карты',
            style: TextStyle(
              fontSize: 12,
              fontWeight: FontWeight.w700,
              color: AppColors.text,
            ),
          ),
          const SizedBox(height: 6),
          _legendRow(
            ChangeClass.retained,
            widget.result.analysisType.shortClassLabels['retained']!,
            MapColors.stroke(
              ChangeClass.retained,
              widget.result.analysisType,
            ),
          ),
          _legendRow(
            ChangeClass.lost,
            widget.result.analysisType.shortClassLabels['lost']!,
            MapColors.stroke(ChangeClass.lost, widget.result.analysisType),
          ),
          _legendRow(
            ChangeClass.newZone,
            widget.result.analysisType.shortClassLabels['new']!,
            MapColors.stroke(ChangeClass.newZone, widget.result.analysisType),
          ),
          const SizedBox(height: 6),
          _unknownLegendRow(),
        ],
      ),
    );
  }

  Widget _legendRow(ChangeClass changeClass, String label, Color color) {
    final bool visible = _visible.contains(changeClass);
    return InkWell(
      onTap: () => _toggle(changeClass),
      child: Padding(
        padding: const EdgeInsets.symmetric(vertical: 4),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: <Widget>[
            Container(
              width: 16,
              height: 16,
              decoration: BoxDecoration(
                color: visible ? AppColors.primary : AppColors.surface,
                borderRadius: BorderRadius.circular(4),
                border: Border.all(
                  color: visible ? AppColors.primary : AppColors.border,
                ),
              ),
              child: visible
                  ? const Icon(Icons.check, size: 12, color: Colors.white)
                  : null,
            ),
            const SizedBox(width: 8),
            Container(
              width: 10,
              height: 10,
              decoration: BoxDecoration(color: color),
            ),
            const SizedBox(width: 6),
            Text(
              label,
              style: const TextStyle(fontSize: 12, color: AppColors.text),
            ),
          ],
        ),
      ),
    );
  }

  Widget _unknownLegendRow() {
    final double? unknown = _unknownCoverage;
    final String label = unknown == null
        ? 'Неизвестно (облака/NoData)'
        : 'Неизвестно ${(unknown * 100).toStringAsFixed(1)}%';
    return InkWell(
      onTap: () => setState(() => _showUnknown = !_showUnknown),
      child: Padding(
        padding: const EdgeInsets.symmetric(vertical: 4),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: <Widget>[
            Container(
              width: 16,
              height: 16,
              decoration: BoxDecoration(
                color: _showUnknown ? AppColors.muted : AppColors.surface,
                borderRadius: BorderRadius.circular(4),
                border: Border.all(
                  color: _showUnknown ? AppColors.muted : AppColors.border,
                ),
              ),
              child: _showUnknown
                  ? const Icon(Icons.check, size: 12, color: Colors.white)
                  : null,
            ),
            const SizedBox(width: 8),
            Container(
              width: 10,
              height: 10,
              decoration: const BoxDecoration(color: AppColors.muted),
            ),
            const SizedBox(width: 6),
            Text(
              label,
              style: const TextStyle(fontSize: 12, color: AppColors.text),
            ),
          ],
        ),
      ),
    );
  }

  Widget _statusBadge() {
    final bool passed =
        widget.result.validationOutcome == ValidationOutcome.passed;
    final int zonesCount = widget.result.aggregate.retained.zonesCount +
        widget.result.aggregate.lost.zonesCount +
        widget.result.aggregate.newZones.zonesCount;
    final bool limited = !passed && zonesCount > 0;
    final Color background = passed ? AppColors.successBg : AppColors.warningBg;
    final Color foreground = passed ? AppColors.successText : AppColors.warningText;
    final IconData icon = passed
        ? Icons.check_circle
        : (limited ? Icons.warning_amber_rounded : Icons.error_outline);
    final String label = passed
        ? 'Статус: Завершено'
        : (limited ? 'Рассчитано с ограничениями' : 'Не прошёл проверку');
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
      decoration: BoxDecoration(
        color: background,
        borderRadius: BorderRadius.circular(20),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: <Widget>[
          Icon(icon, size: 14, color: foreground),
          const SizedBox(width: 6),
          Text(
            label,
            style: TextStyle(
              fontSize: 12,
              fontWeight: FontWeight.w600,
              color: foreground,
            ),
          ),
        ],
      ),
    );
  }
}

/// Публичный разбор GeoJSON-геометрии в кольца координат.
List<List<LatLng>> ringsFromGeometry(Map<String, dynamic> geometry) =>
    _ringsFromGeometry(geometry);

