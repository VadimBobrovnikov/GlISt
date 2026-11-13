import 'package:flutter/widgets.dart';

import 'api_client.dart';

/// Провайдер экземпляра [ApiClient] через [InheritedWidget].
///
/// Позволяет виджетам получать единый клиент без глобальных переменных:
/// `ApiScope.of(context)`.
class ApiScope extends InheritedWidget {
  const ApiScope({
    required this.client,
    required super.child,
    super.key,
  });

  /// Экземпляр HTTP-клиента, общий для дерева виджетов.
  final ApiClient client;

  /// Возвращает клиент из ближайшего [ApiScope] в дереве.
  static ApiClient of(BuildContext context) {
    final ApiScope? scope =
        context.dependOnInheritedWidgetOfExactType<ApiScope>();
    if (scope == null) {
      throw FlutterError(
        'ApiScope не найден в дереве виджетов: оберните MaterialApp в ApiScope.',
      );
    }
    return scope.client;
  }

  @override
  bool updateShouldNotify(ApiScope oldWidget) => oldWidget.client != client;
}
