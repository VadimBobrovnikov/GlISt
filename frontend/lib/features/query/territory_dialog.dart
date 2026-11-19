import 'dart:math' as math;
import 'dart:ui' as ui;

import 'package:flutter/material.dart';
import 'package:flutter_map/flutter_map.dart';
import 'package:latlong2/latlong.dart';

import '../../core/api_client.dart';
import '../../core/app_theme.dart';
import '../../shared/models/models.dart';
import '../map/map_view.dart';

/// Предварительный выбор территории до запуска анализа.
///
/// Возвращает объект `{name, parent, type, bbox, geometry, displayName}` или
/// `null`, если пользователь отказался. При неоднозначности показывает список
/// найденных объектов; при отсутствии границы предлагает выделить участок.
Future<Map<String, dynamic>?> chooseTerritory(
  BuildContext context,
  ApiClient client,
  String text, {
  String? scenarioLabel,
}) async {
  Map<String, dynamic> response;
  try {
    response = await client.resolveTerritory(text: text);
  } on ApiException catch (error) {
    if (!context.mounted) {
      return null;
    }
    await showDialog<void>(
      context: context,
      builder: (_) => _MessageDialog(
        title: 'Не удалось выбрать территорию',
        message: error.message,
      ),
    );
    return null;
  }
  if (!context.mounted) {
    return null;
  }

  var status = response['status'];
  var selected = _selected(response);
  var requested = _asMap(response['requested']);
  for (var attempt = 0; attempt < 2 && status == 'ambiguous'; attempt++) {
    final candidates = _candidates(response);
    final picked = await showDialog<Map<String, dynamic>>(
      context: context,
      builder: (_) => _CandidateDialog(candidates: candidates),
    );
    if (picked == null || !context.mounted) {
      return null;
    }
    response = await client.resolveTerritory(
      text: text,
      name: picked['name']?.toString(),
      parent: requested['parent']?.toString(),
      type: requested['type']?.toString(),
    );
    if (!context.mounted) {
      return null;
    }
    status = response['status'];
    selected = _selected(response);
    requested = _asMap(response['requested']);
  }

  if (status == 'resolved' && selected != null && selected['geometry'] is Map) {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (_) => TerritoryPreviewDialog(
        selected: selected!,
        scenarioLabel: scenarioLabel,
      ),
    );
    return confirmed == true ? selected : null;
  }

  final String serverMessage =
      (response['message'] ?? '').toString().trim();
  final String title;
  if (status == 'not_found') {
    title = 'Объект не найден';
  } else if (status == 'boundary_missing') {
    title = 'Граница не найдена';
  } else if (status == 'point_only') {
    title = 'Найдена только точка';
  } else if (status == 'extraction_failed') {
    title = 'Не удалось разобрать запрос';
  } else if (status == 'geocoder_error') {
    title = 'Геокодер недоступен';
  } else {
    title = 'Не удалось выбрать территорию';
  }
  final String message = serverMessage.isNotEmpty
      ? serverMessage
      : 'Не удалось получить границу объекта. Уточните запрос или выделите '
          'участок на карте вручную.';
  final manual = await showDialog<bool>(
    context: context,
    builder: (_) => _MessageDialog(
      title: title,
      message: message,
      confirmLabel: 'Выбрать участок',
    ),
  );
  if (manual != true || !context.mounted) {
    return null;
  }
  return showDialog<Map<String, dynamic>>(
    context: context,
    builder: (_) => const _ManualAreaDialog(),
  );
}

Map<String, dynamic> _asMap(Object? value) => value is Map<String, dynamic>
    ? value
    : value is Map
        ? Map<String, dynamic>.from(value)
        : <String, dynamic>{};

Map<String, dynamic>? _selected(Map<String, dynamic> response) {
  final raw = _asMap(response['selected']);
  if (raw.isEmpty) {
    return null;
  }
  return <String, dynamic>{
    'name': raw['name'],
    'displayName': raw['displayName'],
    'kind': raw['kind'],
    'city': raw['city'],
    'parent': raw['parent'],
    'type': raw['type'],
    'bbox': raw['bbox'],
    'geometry': raw['geometry'],
    'addresstype': raw['addresstype'],
    'pointOnly': raw['pointOnly'],
    'source': raw['source'],
  };
}

List<Map<String, dynamic>> _candidates(Map<String, dynamic> response) {
  final raw = response['candidates'];
  if (raw is! List) {
    return <Map<String, dynamic>>[];
  }
  return raw
      .whereType<Map<dynamic, dynamic>>()
      .map((Map<dynamic, dynamic> item) => Map<String, dynamic>.from(item))
      .toList(growable: false);
}

List<List<LatLng>> _rings(Map<String, dynamic> selected) {
  final geometry = _asMap(selected['geometry']);
  if (geometry.isNotEmpty) {
    final rings = ringsFromGeometry(geometry);
    if (rings.isNotEmpty) {
      return rings;
    }
  }
  final bbox = _bbox(selected);
  if (bbox == null) {
    return <List<LatLng>>[];
  }
  final west = bbox[0];
  final south = bbox[1];
  final east = bbox[2];
  final north = bbox[3];
  return <List<LatLng>>[
    <LatLng>[
      LatLng(south, west),
      LatLng(south, east),
      LatLng(north, east),
      LatLng(north, west),
      LatLng(south, west),
    ],
  ];
}

List<double>? _bbox(Map<String, dynamic> selected) {
  final raw = selected['bbox'];
  if (raw is! List || raw.length != 4) {
    return null;
  }
  final values = raw.map((Object? item) => item is num ? item.toDouble() : null);
  if (values.contains(null)) {
    return null;
  }
  return values.cast<double>().toList(growable: false);
}

// Адаптивная оболочка диалога: ограничивает размер экраном и держит панель
// действий вне прокручиваемой области, поэтому кнопки и контур остаются
// доступными при любом размере окна и при длинных названиях объектов.
class _DialogShell extends StatelessWidget {
  const _DialogShell({
    required this.header,
    required this.body,
    required this.footer,
  });

  static const double _maxWidth = 760;
  static const double _maxHeight = 640;

  final Widget header;
  final Widget body;
  final Widget footer;

  @override
  Widget build(BuildContext context) {
    return Dialog(
      insetPadding: const EdgeInsets.all(16),
      clipBehavior: Clip.antiAlias,
      child: LayoutBuilder(
        builder: (BuildContext context, BoxConstraints constraints) {
          final double width = constraints.maxWidth.isFinite
              ? math.min(_maxWidth, constraints.maxWidth)
              : _maxWidth;
          final double height = constraints.maxHeight.isFinite
              ? math.min(_maxHeight, constraints.maxHeight)
              : _maxHeight;
          return SizedBox(
            width: width,
            height: height,
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: <Widget>[
                header,
                Expanded(child: body),
                const Divider(height: 1),
                footer,
              ],
            ),
          );
        },
      ),
    );
  }
}

/// Человекочитаемое название типа территории.
String _kindLabel(String kind) {
  switch (kind) {
    case 'city':
      return 'город';
    case 'district':
      return 'административный район';
    case 'microdistrict':
      return 'микрорайон';
    case 'quarter':
      return 'квартал';
    case 'island':
      return 'остров';
    case 'park':
      return 'парк';
    case 'area':
      return 'выбранный участок';
    default:
      return kind;
  }
}

class TerritoryPreviewDialog extends StatelessWidget {
  const TerritoryPreviewDialog({
    super.key,
    required this.selected,
    this.enableTiles = true,
    this.scenarioLabel,
  });

  final Map<String, dynamic> selected;

  /// Подложка карты. Отключается в виджет-тестах, чтобы не обращаться к сети.
  final bool enableTiles;

  /// Сценарий анализа, выбранный пользователем (показывается до запуска).
  final String? scenarioLabel;

  @override
  Widget build(BuildContext context) {
    final List<List<LatLng>> rings = _rings(selected);
    final List<LatLng> points =
        rings.expand((List<LatLng> ring) => ring).toList();
    final String display = (selected['displayName'] ?? selected['name'] ?? '')
        .toString()
        .trim();
    final String parent = (selected['parent'] ?? selected['city'] ?? '')
        .toString()
        .trim();
    final String kind = (selected['kind'] ?? selected['type'] ?? '')
        .toString()
        .trim();
    final List<double>? bbox = _bbox(selected);
    final bool hasContour = points.length >= 2;
    return _DialogShell(
      header: _buildHeader(
        context,
        display: display,
        parent: parent,
        kind: kind,
      ),
      body: _TerritoryBody(
        points: points,
        rings: rings,
        bbox: bbox,
        enableTiles: enableTiles,
      ),
      footer: _buildFooter(context, enabled: hasContour),
    );
  }

  Widget _buildHeader(
    BuildContext context, {
    required String display,
    required String parent,
    required String kind,
  }) {
    final String meta = <String>[
      if (parent.isNotEmpty) 'Город: $parent',
      if (kind.isNotEmpty) 'Тип: ${_kindLabel(kind)}',
    ].join(' · ');
    return Padding(
      padding: const EdgeInsets.fromLTRB(20, 12, 8, 10),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisSize: MainAxisSize.min,
        children: <Widget>[
          Row(
            children: <Widget>[
              const Expanded(
                child: Text(
                  'Проверьте территорию анализа',
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: TextStyle(
                    fontSize: 17,
                    fontWeight: FontWeight.w700,
                    color: AppColors.text,
                  ),
                ),
              ),
              IconButton(
                onPressed: () => Navigator.of(context).pop(false),
                tooltip: 'Закрыть',
                icon: const Icon(Icons.close, size: 18),
                visualDensity: VisualDensity.compact,
              ),
            ],
          ),
          Padding(
            padding: const EdgeInsets.only(right: 12),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              mainAxisSize: MainAxisSize.min,
              children: <Widget>[
                Tooltip(
                  message: display,
                  child: Text(
                    display,
                    maxLines: 3,
                    overflow: TextOverflow.ellipsis,
                    style: const TextStyle(
                      fontSize: 14,
                      height: 1.3,
                      fontWeight: FontWeight.w600,
                      color: AppColors.text,
                    ),
                  ),
                ),
                if (scenarioLabel != null && scenarioLabel!.isNotEmpty) ...<Widget>[
                  const SizedBox(height: 4),
                  Text(
                    'Сценарий: $scenarioLabel',
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: const TextStyle(
                      fontSize: 12,
                      fontWeight: FontWeight.w600,
                      color: AppColors.primary,
                    ),
                  ),
                ],
                if (meta.isNotEmpty) ...<Widget>[
                  const SizedBox(height: 2),
                  Text(
                    meta,
                    maxLines: 2,
                    overflow: TextOverflow.ellipsis,
                    style: const TextStyle(
                      fontSize: 12,
                      color: AppColors.muted,
                    ),
                  ),
                ],
              ],
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildFooter(BuildContext context, {required bool enabled}) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 12, 16, 14),
      child: Wrap(
        alignment: WrapAlignment.end,
        crossAxisAlignment: WrapCrossAlignment.center,
        spacing: 8,
        runSpacing: 8,
        children: <Widget>[
          TextButton(
            onPressed: () => Navigator.of(context).pop(false),
            child: const Text('Отмена'),
          ),
          FilledButton(
            onPressed: enabled ? () => Navigator.of(context).pop(true) : null,
            // Глобальная тема задаёт minimumSize.fromHeight(52): без
            // ограничения кнопка растягивается на всю ширину панели.
            style: FilledButton.styleFrom(
              minimumSize: const Size(0, 46),
              padding: const EdgeInsets.symmetric(horizontal: 22),
            ),
            child: const Text('Подтвердить'),
          ),
        ],
      ),
    );
  }
}
/// Область диалога: карта контура и свёрнутые технические сведения.
///
/// Высота карты учитывает состояние «Дополнительных сведений»: раскрытый блок
/// не выталкивает кнопки и не обрезает координаты. Если места совсем мало,
/// прокручивается только содержимое области, панель действий остаётся видимой.
class _TerritoryBody extends StatefulWidget {
  const _TerritoryBody({
    required this.points,
    required this.rings,
    required this.bbox,
    required this.enableTiles,
  });

  final List<LatLng> points;
  final List<List<LatLng>> rings;
  final List<double>? bbox;
  final bool enableTiles;

  @override
  State<_TerritoryBody> createState() => _TerritoryBodyState();
}

class _TerritoryBodyState extends State<_TerritoryBody> {
  /// Минимальная высота карты: контур должен оставаться видимым.
  static const double _mapMinHeight = 200;
  static const double _collapsedDetailsHeight = 48;
  static const double _expandedDetailsHeight = 132;

  bool _detailsExpanded = false;

  @override
  Widget build(BuildContext context) {
    return LayoutBuilder(
      builder: (BuildContext context, BoxConstraints constraints) {
        final double reserved = _detailsExpanded
            ? _expandedDetailsHeight
            : _collapsedDetailsHeight;
        final bool fits = !constraints.maxHeight.isFinite ||
            constraints.maxHeight - reserved >= _mapMinHeight;
        if (fits) {
          return Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: <Widget>[
              Expanded(child: _buildMap()),
              _buildDetails(context),
            ],
          );
        }
        return SingleChildScrollView(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: <Widget>[
              SizedBox(height: _mapMinHeight, child: _buildMap()),
              _buildDetails(context),
            ],
          ),
        );
      },
    );
  }

  Widget _buildMap() {
    if (widget.points.length < 2) {
      return const Center(
        child: Padding(
          padding: EdgeInsets.all(16),
          child: Text(
            'Контур недоступен — выделите участок вручную.',
            textAlign: TextAlign.center,
            style: TextStyle(fontSize: 13, color: AppColors.muted),
          ),
        ),
      );
    }
    return FlutterMap(
      options: MapOptions(
        initialCameraFit: CameraFit.bounds(
          bounds: LatLngBounds.fromPoints(widget.points),
          padding: const EdgeInsets.all(32),
        ),
        minZoom: 3,
        maxZoom: 18,
      ),
      children: <Widget>[
        if (widget.enableTiles)
          TileLayer(
            urlTemplate: 'https://tile.openstreetmap.org/{z}/{x}/{y}.png',
            userAgentPackageName: 'ru.glist.app',
          ),
        PolygonLayer(
          polygons: widget.rings
              .map(
                (List<LatLng> ring) => Polygon(
                  points: ring,
                  color: AppColors.primary.withValues(alpha: 0.15),
                  borderColor: AppColors.primary,
                  borderStrokeWidth: 2,
                ),
              )
              .toList(growable: false),
        ),
      ],
    );
  }

  Widget _buildDetails(BuildContext context) {
    final List<double>? bbox = widget.bbox;
    return Theme(
      data: Theme.of(context).copyWith(dividerColor: Colors.transparent),
      child: ExpansionTile(
        tilePadding: const EdgeInsets.symmetric(horizontal: 20),
        childrenPadding: const EdgeInsets.fromLTRB(20, 0, 20, 12),
        expandedCrossAxisAlignment: CrossAxisAlignment.start,
        dense: true,
        onExpansionChanged: (bool expanded) {
          setState(() {
            _detailsExpanded = expanded;
          });
        },
        title: const Text(
          'Дополнительные сведения',
          style: TextStyle(
            fontSize: 12.5,
            fontWeight: FontWeight.w600,
            color: AppColors.muted,
          ),
        ),
        children: <Widget>[
          if (bbox != null)
            SelectableText(
              'bbox (WGS84): ${bbox[0].toStringAsFixed(5)}, '
              '${bbox[1].toStringAsFixed(5)} — '
              '${bbox[2].toStringAsFixed(5)}, '
              '${bbox[3].toStringAsFixed(5)}',
              style: const TextStyle(fontSize: 11.5, color: AppColors.muted),
            ),
          const SizedBox(height: 4),
          const Text(
            'Координаты — техническая информация о контуре, на расчёт '
            'не влияют.',
            style: TextStyle(fontSize: 11.5, color: AppColors.muted),
          ),
        ],
      ),
    );
  }
}

class _MessageDialog extends StatelessWidget {
  const _MessageDialog({
    required this.title,
    required this.message,
    this.confirmLabel,
  });

  final String title;
  final String message;
  final String? confirmLabel;

  @override
  Widget build(BuildContext context) {
    return AlertDialog(
      title: Text(title),
      content: ConstrainedBox(
        constraints: const BoxConstraints(maxWidth: 520),
        child: Text(message),
      ),
      actions: <Widget>[
        TextButton(
          onPressed: () => Navigator.of(context).pop(false),
          child: const Text('Отмена'),
        ),
        if (confirmLabel != null)
          FilledButton(
            onPressed: () => Navigator.of(context).pop(true),
            child: Text(confirmLabel!),
          ),
      ],
    );
  }
}

class _TerritoryThumbnail extends StatelessWidget {
  const _TerritoryThumbnail({required this.geometry, required this.bbox});

  final Map<String, dynamic> geometry;
  final List<double>? bbox;

  @override
  Widget build(BuildContext context) {
    final List<List<LatLng>> rings;
    if (geometry.isNotEmpty) {
      rings = ringsFromGeometry(geometry);
    } else if (bbox != null && bbox!.length == 4) {
      rings = <List<LatLng>>[
        <LatLng>[
          LatLng(bbox![1], bbox![0]),
          LatLng(bbox![1], bbox![2]),
          LatLng(bbox![3], bbox![2]),
          LatLng(bbox![3], bbox![0]),
          LatLng(bbox![1], bbox![0]),
        ],
      ];
    } else {
      rings = <List<LatLng>>[];
    }
    return Container(
      decoration: BoxDecoration(
        border: Border.all(color: AppColors.border),
        borderRadius: BorderRadius.circular(6),
        color: AppColors.accentSoft,
      ),
      child: rings.isEmpty
          ? const Icon(Icons.place_outlined, size: 18, color: AppColors.muted)
          : CustomPaint(painter: _TerritoryShapePainter(rings)),
    );
  }
}

class _TerritoryShapePainter extends CustomPainter {
  _TerritoryShapePainter(this.rings);

  final List<List<LatLng>> rings;

  @override
  void paint(Canvas canvas, Size size) {
    final points = rings.expand((List<LatLng> ring) => ring).toList();
    if (points.length < 3) {
      return;
    }
    var minLat = points.first.latitude;
    var maxLat = points.first.latitude;
    var minLon = points.first.longitude;
    var maxLon = points.first.longitude;
    for (final LatLng point in points) {
      if (point.latitude < minLat) minLat = point.latitude;
      if (point.latitude > maxLat) maxLat = point.latitude;
      if (point.longitude < minLon) minLon = point.longitude;
      if (point.longitude > maxLon) maxLon = point.longitude;
    }
    final spanLat = (maxLat - minLat).abs() < 1e-9 ? 1e-9 : maxLat - minLat;
    final spanLon = (maxLon - minLon).abs() < 1e-9 ? 1e-9 : maxLon - minLon;
    const padding = 3.0;
    final scale = (size.width - 2 * padding) / spanLon;
    final scaleY = (size.height - 2 * padding) / spanLat;
    final useScale = scale < scaleY ? scale : scaleY;
    Offset project(LatLng point) => Offset(
      padding + (point.longitude - minLon) * useScale,
      size.height - padding - (point.latitude - minLat) * useScale,
    );
    final path = ui.Path();
    for (final List<LatLng> ring in rings) {
      if (ring.length < 3) {
        continue;
      }
      path.moveTo(project(ring.first).dx, project(ring.first).dy);
      for (final LatLng point in ring.skip(1)) {
        final offset = project(point);
        path.lineTo(offset.dx, offset.dy);
      }
      path.close();
    }
    canvas.drawPath(
      path,
      Paint()
        ..style = PaintingStyle.fill
        ..color = AppColors.primary.withValues(alpha: 0.2),
    );
    canvas.drawPath(
      path,
      Paint()
        ..style = PaintingStyle.stroke
        ..strokeWidth = 1
        ..color = AppColors.primary,
    );
  }

  @override
  bool shouldRepaint(_TerritoryShapePainter oldDelegate) => false;
}

class _CandidateDialog extends StatelessWidget {
  const _CandidateDialog({required this.candidates});

  final List<Map<String, dynamic>> candidates;

  @override
  Widget build(BuildContext context) {
    return AlertDialog(
      title: const Text('Выберите объект'),
      content: SizedBox(
        width: 620,
        child: ListView.separated(
          shrinkWrap: true,
          itemCount: candidates.length,
          separatorBuilder: (BuildContext context, int index) =>
              const Divider(height: 1),
          itemBuilder: (BuildContext context, int index) {
            final candidate = candidates[index];
            final title = (candidate['displayName'] ?? candidate['name'] ?? '')
                .toString();
            final kind = (candidate['kind'] ?? candidate['addresstype'] ?? '')
                .toString();
            final city = (candidate['city'] ?? candidate['parent'] ?? '')
                .toString();
            final subtitle = <String>[
              if (kind.isNotEmpty) kind,
              if (city.isNotEmpty) city,
            ].join(' · ');
            return ListTile(
              leading: SizedBox(
                width: 64,
                height: 48,
                child: _TerritoryThumbnail(
                  geometry: _asMap(candidate['geometry']),
                  bbox: _bbox(candidate),
                ),
              ),
              title: Text(title, style: const TextStyle(fontSize: 13)),
              subtitle: subtitle.isEmpty ? null : Text(subtitle),
              onTap: () => Navigator.of(context).pop(candidate),
            );
          },
        ),
      ),
      actions: <Widget>[
        TextButton(
          onPressed: () => Navigator.of(context).pop(),
          child: const Text('Отмена'),
        ),
      ],
    );
  }
}


class _ManualAreaDialog extends StatefulWidget {
  const _ManualAreaDialog();

  @override
  State<_ManualAreaDialog> createState() => _ManualAreaDialogState();
}

class _ManualAreaDialogState extends State<_ManualAreaDialog> {
  final MapController _controller = MapController();
  final List<LatLng> _points = <LatLng>[];

  void _handleTap(TapPosition position, LatLng point) {
    setState(() {
      if (_points.length >= 2) {
        _points.clear();
      }
      _points.add(point);
    });
  }

  List<LatLng> get _rectangle {
    if (_points.length < 2) {
      return <LatLng>[];
    }
    final south = _points[0].latitude < _points[1].latitude
        ? _points[0].latitude
        : _points[1].latitude;
    final north = _points[0].latitude < _points[1].latitude
        ? _points[1].latitude
        : _points[0].latitude;
    final west = _points[0].longitude < _points[1].longitude
        ? _points[0].longitude
        : _points[1].longitude;
    final east = _points[0].longitude < _points[1].longitude
        ? _points[1].longitude
        : _points[0].longitude;
    return <LatLng>[
      LatLng(south, west),
      LatLng(south, east),
      LatLng(north, east),
      LatLng(north, west),
      LatLng(south, west),
    ];
  }

  bool get _valid {
    final rectangle = _rectangle;
    if (rectangle.isEmpty) {
      return false;
    }
    final west = rectangle[0].longitude;
    final east = rectangle[1].longitude;
    final south = rectangle[0].latitude;
    final north = rectangle[2].latitude;
    return (east - west) > 0.0005 && (north - south) > 0.0005;
  }

  void _confirm() {
    final rectangle = _rectangle;
    final west = rectangle[0].longitude;
    final south = rectangle[0].latitude;
    final east = rectangle[2].longitude;
    final north = rectangle[2].latitude;
    Navigator.of(context).pop(<String, dynamic>{
      'name': 'Выбранный участок',
      'displayName': 'Выбранный участок',
      'parent': null,
      'type': 'area',
      'bbox': <double>[west, south, east, north],
      'geometry': <String, dynamic>{
        'type': 'Polygon',
        'coordinates': <dynamic>[
          <dynamic>[
            <double>[west, south],
            <double>[east, south],
            <double>[east, north],
            <double>[west, north],
            <double>[west, south],
          ],
        ],
      },
    });
  }


  @override
  Widget build(BuildContext context) {
    final List<LatLng> rectangle = _rectangle;
    final String hint = _points.isEmpty
        ? 'Нажмите две противоположные точки на карте'
        : 'Точек: ${_points.length} из 2';
    return _DialogShell(
      header: Padding(
        padding: const EdgeInsets.fromLTRB(20, 16, 8, 10),
        child: Row(
          children: <Widget>[
            const Expanded(
              child: Text(
                'Выделите участок',
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: TextStyle(
                  fontSize: 17,
                  fontWeight: FontWeight.w700,
                  color: AppColors.text,
                ),
              ),
            ),
            IconButton(
              onPressed: () => Navigator.of(context).pop(),
              tooltip: 'Закрыть',
              icon: const Icon(Icons.close, size: 18),
              visualDensity: VisualDensity.compact,
            ),
          ],
        ),
      ),
      body: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 16),
        child: ClipRRect(
          borderRadius: BorderRadius.circular(8),
          child: FlutterMap(
            mapController: _controller,
            options: MapOptions(
              initialCenter: const LatLng(55.0, 90.0),
              initialZoom: 3,
              minZoom: 3,
              maxZoom: 18,
              onTap: _handleTap,
            ),
            children: <Widget>[
              TileLayer(
                urlTemplate: 'https://tile.openstreetmap.org/{z}/{x}/{y}.png',
                userAgentPackageName: 'ru.glist.app',
              ),
              if (rectangle.isNotEmpty)
                PolygonLayer(
                  polygons: <Polygon>[
                    Polygon(
                      points: rectangle,
                      color: AppColors.primary.withValues(alpha: 0.15),
                      borderColor: AppColors.primary,
                      borderStrokeWidth: 2,
                    ),
                  ],
                ),
            ],
          ),
        ),
      ),
      footer: Padding(
        padding: const EdgeInsets.fromLTRB(16, 12, 16, 14),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          mainAxisSize: MainAxisSize.min,
          children: <Widget>[
            Text(
              hint,
              maxLines: 2,
              overflow: TextOverflow.ellipsis,
              style: const TextStyle(fontSize: 12, color: AppColors.muted),
            ),
            const SizedBox(height: 8),
            Wrap(
              alignment: WrapAlignment.end,
              crossAxisAlignment: WrapCrossAlignment.center,
              spacing: 8,
              runSpacing: 8,
              children: <Widget>[
                TextButton(
                  onPressed: () => Navigator.of(context).pop(),
                  child: const Text('Отмена'),
                ),
                FilledButton(
                  onPressed: _valid ? _confirm : null,
                  style: FilledButton.styleFrom(
                    minimumSize: const Size(0, 46),
                    padding: const EdgeInsets.symmetric(horizontal: 20),
                  ),
                  child: const Text('Использовать участок'),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }
}

