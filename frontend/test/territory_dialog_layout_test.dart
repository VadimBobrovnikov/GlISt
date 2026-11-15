// Проверка адаптивной вёрстки диалога подтверждения территории.
//
// Регрессия: диалог имел жёсткий размер 760x620, а bbox выводился в Row рядом
// с кнопками через Expanded. При узком окне фиксированные кнопки забирали всю
// ширину, Expanded получал 0, текст переносился по одному символу, нижняя
// панель вырастала и выталкивала карту и кнопки за пределы диалога.
//
// Тесты фиксируют инварианты: на разных размерах окна контур виден, кнопки
// доступны, технические координаты скрыты в дополнительных сведениях.
import 'package:flutter/material.dart';
import 'package:flutter_map/flutter_map.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:glist/core/app_theme.dart';
import 'package:glist/features/query/territory_dialog.dart';

Map<String, dynamic> _territory({String name = 'Восточный'}) {
  return <String, dynamic>{
    'name': name,
    'displayName': name,
    'kind': 'microdistrict',
    'parent': 'Улан-Удэ',
    'type': 'microdistrict',
    'bbox': <double>[107.73186, 51.85512, 107.76142, 51.87632],
    'geometry': <String, dynamic>{
      'type': 'Polygon',
      'coordinates': <dynamic>[
        <dynamic>[
          <double>[107.73186, 51.85512],
          <double>[107.76142, 51.85512],
          <double>[107.76142, 51.87632],
          <double>[107.73186, 51.87632],
          <double>[107.73186, 51.85512],
        ],
      ],
    },
  };
}

Future<void> _pumpDialog(
  WidgetTester tester,
  Size size,
  Map<String, dynamic> selected,
) async {
  tester.view.physicalSize = size;
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.resetPhysicalSize);
  addTearDown(tester.view.resetDevicePixelRatio);
  await tester.pumpWidget(
    MaterialApp(
      theme: buildGlistTheme(),
      home: Scaffold(
        body: TerritoryPreviewDialog(selected: selected, enableTiles: false),
      ),
    ),
  );
  await tester.pump(const Duration(milliseconds: 300));
}

void _expectActionsVisible(WidgetTester tester, Size size) {
  expect(find.text('Отмена'), findsOneWidget);
  expect(find.text('Подтвердить'), findsOneWidget);
  for (final Finder finder in <Finder>[
    find.text('Отмена'),
    find.text('Подтвердить'),
  ]) {
    final Rect rect = tester.getRect(finder);
    expect(rect.left, greaterThanOrEqualTo(0));
    expect(rect.right, lessThanOrEqualTo(size.width));
    expect(rect.top, greaterThanOrEqualTo(0));
    expect(rect.bottom, lessThanOrEqualTo(size.height));
  }
}

void main() {
  const List<Size> sizes = <Size>[
    Size(1440, 900),
    Size(1024, 700),
    Size(800, 560),
    Size(480, 720),
    Size(900, 420),
  ];

  for (final Size size in sizes) {
    testWidgets(
      'диалог территории пригоден при окне '
      '${size.width.toInt()}x${size.height.toInt()}',
      (WidgetTester tester) async {
        await _pumpDialog(tester, size, _territory());

        expect(tester.takeException(), isNull);
        _expectActionsVisible(tester, size);

        // Кнопка не должна растягиваться на всю ширину (тема задаёт
        // minimumSize.fromHeight) и при достаточной ширине стоять в одной строке.
        final Rect confirmRect = tester.getRect(
          find.widgetWithText(FilledButton, 'Подтвердить'),
        );
        expect(confirmRect.width, greaterThan(80));
        expect(confirmRect.width, lessThan(size.width * 0.6));
        if (size.width >= 800) {
          final Rect cancelRect = tester.getRect(
            find.widgetWithText(TextButton, 'Отмена'),
          );
          expect((cancelRect.center.dy - confirmRect.center.dy).abs(),
              lessThan(6));
        }

        final Finder map = find.byType(FlutterMap);
        expect(map, findsOneWidget);
        expect(tester.getRect(map).height, greaterThanOrEqualTo(150));

        expect(find.text('Восточный'), findsOneWidget);
        expect(find.textContaining('Улан-Удэ'), findsWidgets);
        expect(find.textContaining('микрорайон'), findsWidgets);

        // Технические координаты не занимают основную область диалога.
        expect(find.textContaining('bbox'), findsNothing);
        final Finder details = find.text('Дополнительные сведения');
        await tester.ensureVisible(details);
        await tester.pump(const Duration(milliseconds: 200));
        await tester.tap(details);
        await tester.pump(const Duration(milliseconds: 400));
        expect(find.textContaining('bbox'), findsOneWidget);
        expect(tester.takeException(), isNull);
      },
    );
  }

  testWidgets('раскрытые дополнительные сведения не обрезают координаты', (
    WidgetTester tester,
  ) async {
    const Size size = Size(1440, 900);
    await _pumpDialog(tester, size, _territory());

    await tester.tap(find.text('Дополнительные сведения'));
    await tester.pump(const Duration(milliseconds: 500));

    final Finder bbox = find.textContaining('bbox');
    expect(bbox, findsOneWidget);
    final Rect rect = tester.getRect(bbox);
    expect(rect.bottom, lessThanOrEqualTo(size.height));
    expect(rect.height, greaterThan(8));
    _expectActionsVisible(tester, size);
    expect(tester.takeException(), isNull);
  });

  testWidgets('в низком окне координаты доступны, кнопки остаются видны', (
    WidgetTester tester,
  ) async {
    const Size size = Size(900, 420);
    await _pumpDialog(tester, size, _territory());

    final Finder details = find.text('Дополнительные сведения');
    await tester.ensureVisible(details);
    await tester.tap(details);
    await tester.pump(const Duration(milliseconds: 500));

    final Finder bbox = find.textContaining('bbox');
    await tester.ensureVisible(bbox);
    await tester.pump(const Duration(milliseconds: 200));
    expect(tester.getRect(bbox).bottom, lessThanOrEqualTo(size.height));
    _expectActionsVisible(tester, size);
    expect(tester.takeException(), isNull);
  });

  testWidgets('длинное название и маленькое окно не ломают диалог', (
    WidgetTester tester,
  ) async {
    const Size size = Size(420, 640);
    const String longName =
        'Микрорайон Восточный, территория бывшего посёлка '
        'Стеклозавод и прилегающие садовые товарищества, Улан-Удэ';
    await _pumpDialog(tester, size, _territory(name: longName));

    expect(tester.takeException(), isNull);
    _expectActionsVisible(tester, size);
    expect(find.byType(FlutterMap), findsOneWidget);
    expect(tester.getRect(find.byType(FlutterMap)).height, greaterThanOrEqualTo(150));
  });

  testWidgets('без контура кнопка подтверждения недоступна', (
    WidgetTester tester,
  ) async {
    const Size size = Size(900, 620);
    await _pumpDialog(
      tester,
      size,
      <String, dynamic>{
        'name': 'Неизвестный объект',
        'displayName': 'Неизвестный объект',
        'type': 'other',
      },
    );
    expect(tester.takeException(), isNull);
    expect(find.textContaining('Контур недоступен'), findsOneWidget);
    final FilledButton button = tester.widget<FilledButton>(
      find.widgetWithText(FilledButton, 'Подтвердить'),
    );
    expect(button.onPressed, isNull);
  });
}
