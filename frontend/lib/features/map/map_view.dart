import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_map/flutter_map.dart';
import 'package:latlong2/latlong.dart';

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
class MapColors {
  const MapColors._();

  /// Цвет контура зоны.
  static Color stroke(ChangeClass changeClass) {
    switch (changeClass) {
      case ChangeClass.retained:
        return AppColors.retained;
      case ChangeClass.lost:
        return AppColors.lost;
      case ChangeClass.newZone:
        return AppColors.newZone;
    }
  }

  /// Цвет заливки зоны (40% прозрачности).
  static Color fill(ChangeClass changeClass) =>
      stroke(changeClass).withValues(alpha: 0.4);
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
    final List<LatLng> allPoints = <LatLng>[];
    final List<Polygon> polygons = <Polygon>[];
    for (final ChangeZone zone in zones) {
      for (final List<LatLng> ring in zone.rings) {
        allPoints.addAll(ring);
        polygons.add(
          Polygon(
            points: ring,
            color: MapColors.fill(zone.changeClass),
            borderColor: MapColors.stroke(zone.changeClass),
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
      );
    } else {
      options = const MapOptions(
        initialCenter: LatLng(55.751244, 37.618423),
        initialZoom: 10,
        minZoom: 3,
        maxZoom: 18,
      );
    }
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
            PolygonLayer(polygons: polygons),
          ],
        ),
        Positioned(top: 16, left: 16, child: _analysisChip()),
        Positioned(top: 16, right: 16, child: _zoomControls()),
        Positioned(left: 16, bottom: 16, child: _legend()),
        Positioned(right: 16, bottom: 16, child: _statusBadge()),
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
          _legendRow(ChangeClass.retained, 'Сохранившиеся', AppColors.retained),
          _legendRow(ChangeClass.lost, 'Потерянные', AppColors.lost),
          _legendRow(ChangeClass.newZone, 'Новые зоны', AppColors.newZone),
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

  Widget _statusBadge() {
    final bool passed =
        widget.result.validationOutcome == ValidationOutcome.passed;
    final Color background = passed ? AppColors.successBg : AppColors.warningBg;
    final Color foreground = passed ? AppColors.successText : AppColors.warningText;
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
      decoration: BoxDecoration(
        color: background,
        borderRadius: BorderRadius.circular(20),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: <Widget>[
          Icon(Icons.check_circle, size: 14, color: foreground),
          const SizedBox(width: 6),
          Text(
            passed ? 'Статус: Завершено' : 'Статус: Не прошёл проверку',
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
