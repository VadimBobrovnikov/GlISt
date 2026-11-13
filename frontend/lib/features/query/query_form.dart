import 'dart:async';

import 'package:flutter/material.dart';

import '../../core/api_client.dart';
import '../../core/api_provider.dart';
import '../../core/app_theme.dart';
import '../../shared/models/models.dart';
import '../progress/progress_view.dart';

/// Экран ввода запроса на естественном языке (T067).
///
/// Отправляет `POST /api/v1/queries` через [ApiClient] и переходит на экран
/// прогресса анализа.
class QueryFormScreen extends StatefulWidget {
  const QueryFormScreen({super.key});

  @override
  State<QueryFormScreen> createState() => _QueryFormScreenState();
}

class _QueryFormScreenState extends State<QueryFormScreen> {
  final TextEditingController _controller = TextEditingController();
  bool _submitting = false;
  String? _error;

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  void _applyTemplate(String query) {
    _controller.text = query;
    _controller.selection = TextSelection.collapsed(offset: query.length);
    if (_error != null) {
      setState(() {
        _error = null;
      });
    }
  }

  void _handleTextChanged(String value) {
    if (_error != null) {
      setState(() {
        _error = null;
      });
    }
  }

  Future<void> _submit() async {
    final String text = _controller.text.trim();
    if (text.length < 3) {
      setState(() {
        _error = 'Введите запрос не короче 3 символов';
      });
      return;
    }
    final ApiClient client = ApiScope.of(context);
    final NavigatorState navigator = Navigator.of(context);
    setState(() {
      _submitting = true;
      _error = null;
    });
    try {
      final QueryCreated created = await client.createQuery(text: text);
      if (!mounted) {
        return;
      }
      await navigator.push(
        MaterialPageRoute<void>(
          builder: (_) => ProgressScreen(
            runId: created.run.id,
            queryText: text,
          ),
        ),
      );
      if (!mounted) {
        return;
      }
      setState(() {
        _submitting = false;
      });
    } on ApiException catch (error) {
      if (!mounted) {
        return;
      }
      setState(() {
        _error = error.message;
        _submitting = false;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final TextTheme textTheme = Theme.of(context).textTheme;
    return Scaffold(
      body: SafeArea(
        child: SingleChildScrollView(
          padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 32),
          child: Center(
            child: ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 760),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: <Widget>[
                  const _BrandHeader(),
                  const SizedBox(height: 32),
                  Text(
                    'Узнайте как.',
                    style: textTheme.displaySmall?.copyWith(
                      fontWeight: FontWeight.w700,
                      color: AppColors.text,
                    ),
                  ),
                  const SizedBox(height: 12),
                  const Text(
                    'Спрашивайте на обычном языке. ИИ-агенты найдут '
                    'спутниковые данные, проведут анализ и покажут результат '
                    'на карте.',
                    style: TextStyle(
                      fontSize: 15,
                      height: 1.5,
                      color: AppColors.muted,
                    ),
                  ),
                  const SizedBox(height: 24),
                  _buildInputCard(),
                  const SizedBox(height: 28),
                  Text(
                    'Начните с готового сценария',
                    style: textTheme.titleMedium?.copyWith(
                      fontWeight: FontWeight.w700,
                      color: AppColors.text,
                    ),
                  ),
                  const SizedBox(height: 12),
                  _buildTemplates(),
                  const SizedBox(height: 24),
                  const Center(
                    child: Text(
                      '4 ИИ-агента работают вместе для вас',
                      style: TextStyle(fontSize: 13, color: AppColors.muted),
                    ),
                  ),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }

  Widget _buildInputCard() {
    final String? error = _error;
    return Container(
      padding: const EdgeInsets.all(24),
      decoration: BoxDecoration(
        color: AppColors.surface,
        borderRadius: BorderRadius.circular(20),
        border: Border.all(color: AppColors.border),
        boxShadow: <BoxShadow>[
          BoxShadow(
            color: Colors.black.withValues(alpha: 0.04),
            blurRadius: 18,
            offset: const Offset(0, 6),
          ),
        ],
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          const Row(
            children: <Widget>[
              Icon(Icons.auto_awesome, color: AppColors.primary, size: 22),
              SizedBox(width: 10),
              Expanded(
                child: Text(
                  'Что вы хотите проанализировать?',
                  style: TextStyle(
                    fontSize: 18,
                    fontWeight: FontWeight.w600,
                    color: AppColors.text,
                  ),
                ),
              ),
            ],
          ),
          const SizedBox(height: 16),
          TextField(
            controller: _controller,
            minLines: 4,
            maxLines: 8,
            maxLength: 1000,
            textInputAction: TextInputAction.newline,
            style: const TextStyle(fontSize: 15, height: 1.5),
            decoration: const InputDecoration(
              hintText: 'Например: Как изменилась площадь зелёных зон '
                  'Москвы за 5 лет?',
              hintMaxLines: 2,
            ),
            onChanged: _handleTextChanged,
          ),
          const Row(
            children: <Widget>[
              Icon(Icons.info_outline, size: 16, color: AppColors.muted),
              SizedBox(width: 8),
              Expanded(
                child: Text(
                  'Укажите место и период для более точного результата',
                  style: TextStyle(fontSize: 13, color: AppColors.muted),
                ),
              ),
            ],
          ),
          if (error != null) ...<Widget>[
            const SizedBox(height: 12),
            Text(
              error,
              style: const TextStyle(color: AppColors.lost, fontSize: 13),
            ),
          ],
          const SizedBox(height: 16),
          FilledButton(
            onPressed: _submitting
                ? null
                : () {
                    unawaited(_submit());
                  },
            child: _submitting
                ? const SizedBox(
                    width: 20,
                    height: 20,
                    child: CircularProgressIndicator(
                      strokeWidth: 2,
                      color: Colors.white,
                    ),
                  )
                : const Row(
                    mainAxisAlignment: MainAxisAlignment.center,
                    children: <Widget>[
                      Icon(Icons.auto_awesome, size: 18),
                      SizedBox(width: 10),
                      Text('Запустить анализ'),
                      SizedBox(width: 10),
                      Icon(Icons.arrow_forward, size: 18),
                    ],
                  ),
          ),
        ],
      ),
    );
  }

  Widget _buildTemplates() {
    return LayoutBuilder(
      builder: (BuildContext context, BoxConstraints constraints) {
        final bool narrow = constraints.maxWidth < 640;
        final double cardWidth = narrow
            ? constraints.maxWidth
            : (constraints.maxWidth - 32) / 3;
        return Wrap(
          spacing: 16,
          runSpacing: 16,
          children: _templates
              .map(
                (_TemplateSpec spec) => SizedBox(
                  width: cardWidth,
                  child: _TemplateCard(
                    spec: spec,
                    onTap: () => _applyTemplate(spec.query),
                  ),
                ),
              )
              .toList(growable: false),
        );
      },
    );
  }
}

class _TemplateSpec {
  const _TemplateSpec({
    required this.icon,
    required this.title,
    required this.subtitle,
    required this.query,
  });

  final IconData icon;
  final String title;
  final String subtitle;
  final String query;
}

const List<_TemplateSpec> _templates = <_TemplateSpec>[
  _TemplateSpec(
    icon: Icons.eco_outlined,
    title: 'Изменение растительности',
    subtitle: 'Как меняются зелёные зоны',
    query: 'Как изменилась площадь зелёных зон Москвы за 5 лет?',
  ),
  _TemplateSpec(
    icon: Icons.water_drop_outlined,
    title: 'Зоны затопления',
    subtitle: 'Оценка водных территорий',
    query: 'Покажи зоны затопления в Подмосковье за 2023 год',
  ),
  _TemplateSpec(
    icon: Icons.apartment_outlined,
    title: 'Анализ застройки',
    subtitle: 'Динамика городской среды',
    query: 'Как изменилась застройка центра Казани за 3 года?',
  ),
];

class _TemplateCard extends StatelessWidget {
  const _TemplateCard({required this.spec, required this.onTap});

  final _TemplateSpec spec;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return Material(
      color: AppColors.surface,
      borderRadius: BorderRadius.circular(16),
      child: InkWell(
        onTap: onTap,
        borderRadius: BorderRadius.circular(16),
        child: Container(
          padding: const EdgeInsets.all(16),
          decoration: BoxDecoration(
            borderRadius: BorderRadius.circular(16),
            border: Border.all(color: AppColors.border),
          ),
          child: Row(
            children: <Widget>[
              Container(
                width: 40,
                height: 40,
                decoration: BoxDecoration(
                  color: AppColors.accentSoft,
                  borderRadius: BorderRadius.circular(12),
                ),
                child: Icon(spec.icon, color: AppColors.primary, size: 20),
              ),
              const SizedBox(width: 12),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: <Widget>[
                    Text(
                      spec.title,
                      style: const TextStyle(
                        fontSize: 14,
                        fontWeight: FontWeight.w600,
                        color: AppColors.text,
                      ),
                    ),
                    const SizedBox(height: 2),
                    Text(
                      spec.subtitle,
                      style: const TextStyle(
                        fontSize: 12,
                        color: AppColors.muted,
                      ),
                    ),
                  ],
                ),
              ),
              const Icon(Icons.arrow_forward, size: 16, color: AppColors.muted),
            ],
          ),
        ),
      ),
    );
  }
}

class _BrandHeader extends StatelessWidget {
  const _BrandHeader();

  @override
  Widget build(BuildContext context) {
    return Row(
      children: <Widget>[
        Container(
          width: 40,
          height: 40,
          decoration: BoxDecoration(
            color: AppColors.primary,
            borderRadius: BorderRadius.circular(10),
          ),
          child: const Icon(Icons.layers, color: Colors.white, size: 22),
        ),
        const SizedBox(width: 12),
        const Text(
          'GlISt.',
          style: TextStyle(
            fontSize: 24,
            fontWeight: FontWeight.w700,
            color: AppColors.text,
          ),
        ),
      ],
    );
  }
}
