// Smoke-тест запуска клиента GlISt (задача T009).
//
// Проверяет, что приложение собирается и рендерит базовый экран (MaterialApp + Scaffold).
// Намеренно не завязан на конкретном содержимом главной страницы: UI меняется по задачам
// пользовательских историй, smoke должен оставаться зелёным на каждом этапе.
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:glist/main.dart';

void main() {
  testWidgets('клиент запускается: MyApp рендерит MaterialApp со Scaffold', (
    WidgetTester tester,
  ) async {
    await tester.pumpWidget(const MyApp());

    expect(find.byType(MaterialApp), findsOneWidget);
    expect(find.byType(Scaffold), findsOneWidget);
  });
}
