import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:glist/features/map/map_view.dart';
import 'package:glist/shared/models/models.dart';

/// Геометрия зон должна сохранять вырезы: иначе карта заливает «дырки» и
/// показывает изменения там, где их нет (площади при этом считает PostGIS).
void main() {
  test('полигон с вырезом разбирается в контур + hole', () {
    final String raw = jsonEncode(<String, dynamic>{
      'type': 'FeatureCollection',
      'features': <dynamic>[
        <String, dynamic>{
          'type': 'Feature',
          'properties': <String, dynamic>{
            'change_class': 'new',
            'area_km2': 1.5,
          },
          'geometry': <String, dynamic>{
            'type': 'Polygon',
            'coordinates': <dynamic>[
              <dynamic>[
                <dynamic>[30.0, 50.0],
                <dynamic>[31.0, 50.0],
                <dynamic>[31.0, 51.0],
                <dynamic>[30.0, 51.0],
                <dynamic>[30.0, 50.0],
              ],
              <dynamic>[
                <dynamic>[30.4, 50.4],
                <dynamic>[30.6, 50.4],
                <dynamic>[30.6, 50.6],
                <dynamic>[30.4, 50.6],
                <dynamic>[30.4, 50.4],
              ],
            ],
          },
        },
      ],
    });

    final List<ChangeZone> zones = parseChangeZones(raw);

    expect(zones, hasLength(1));
    expect(zones.single.changeClass, ChangeClass.newZone);
    expect(zones.single.parts, hasLength(1));
    expect(zones.single.parts.single.outer, hasLength(5));
    expect(zones.single.parts.single.holes, hasLength(1));
    expect(zones.single.parts.single.holes.single, hasLength(5));
  });

  test('MultiPolygon даёт отдельные части с собственными вырезами', () {
    final String raw = jsonEncode(<String, dynamic>{
      'type': 'FeatureCollection',
      'features': <dynamic>[
        <String, dynamic>{
          'type': 'Feature',
          'properties': <String, dynamic>{'change_class': 'lost'},
          'geometry': <String, dynamic>{
            'type': 'MultiPolygon',
            'coordinates': <dynamic>[
              <dynamic>[
                <dynamic>[
                  <dynamic>[30.0, 50.0],
                  <dynamic>[30.2, 50.0],
                  <dynamic>[30.2, 50.2],
                  <dynamic>[30.0, 50.0],
                ],
              ],
              <dynamic>[
                <dynamic>[
                  <dynamic>[31.0, 51.0],
                  <dynamic>[31.2, 51.0],
                  <dynamic>[31.2, 51.2],
                  <dynamic>[31.0, 51.0],
                ],
              ],
            ],
          },
        },
      ],
    });

    final List<ChangeZone> zones = parseChangeZones(raw);

    expect(zones.single.parts, hasLength(2));
    expect(zones.single.parts.first.holes, isEmpty);
    expect(zones.single.changeClass, ChangeClass.lost);
  });

  test('запись без известного класса пропускается', () {
    final String raw = jsonEncode(<String, dynamic>{
      'type': 'FeatureCollection',
      'features': <dynamic>[
        <String, dynamic>{
          'type': 'Feature',
          'properties': <String, dynamic>{'change_class': 'unknown_class'},
          'geometry': <String, dynamic>{
            'type': 'Polygon',
            'coordinates': <dynamic>[],
          },
        },
      ],
    });

    expect(parseChangeZones(raw), isEmpty);
  });
}
