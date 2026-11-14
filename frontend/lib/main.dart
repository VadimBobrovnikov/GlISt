import 'package:flutter/material.dart';

import 'core/api_client.dart';
import 'core/api_provider.dart';
import 'core/app_theme.dart';
import 'features/query/query_form.dart';
import 'features/result/result_view.dart';

void main() {
  runApp(const MyApp());
}

/// Корневой виджет GlISt: тема, HTTP-клиент и навигация.
class MyApp extends StatefulWidget {
  const MyApp({super.key});

  @override
  State<MyApp> createState() => _MyAppState();
}

class _MyAppState extends State<MyApp> {
  final ApiClient _client = ApiClient();

  /// Прямая ссылка на результат: `#/result/<runId>`.
  String? get _deepLinkRunId {
    final RegExpMatch? match = RegExp(
      r'^/?result/([0-9a-fA-F-]{36})$',
    ).firstMatch(Uri.base.fragment);
    return match?.group(1);
  }

  @override
  Widget build(BuildContext context) {
    final String? runId = _deepLinkRunId;
    return ApiScope(
      client: _client,
      child: MaterialApp(
        title: 'GlISt — геопространственный анализ',
        debugShowCheckedModeBanner: false,
        theme: buildGlistTheme(),
        home: runId != null
            ? ResultScreen(runId: runId)
            : const QueryFormScreen(),
      ),
    );
  }
}
