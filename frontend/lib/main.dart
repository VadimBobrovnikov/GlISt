import 'package:flutter/material.dart';

import 'core/api_client.dart';
import 'core/api_provider.dart';
import 'core/app_theme.dart';
import 'features/query/query_form.dart';

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

  @override
  Widget build(BuildContext context) {
    return ApiScope(
      client: _client,
      child: MaterialApp(
        title: 'GlISt — геопространственный анализ',
        debugShowCheckedModeBanner: false,
        theme: buildGlistTheme(),
        home: const QueryFormScreen(),
      ),
    );
  }
}
