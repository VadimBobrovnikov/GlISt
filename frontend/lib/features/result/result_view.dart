import 'dart:async';

import 'package:flutter/material.dart';

import '../../core/api_client.dart';
import '../../core/api_provider.dart';
import '../../core/app_theme.dart';
import '../../shared/models/models.dart';
import '../export/export_button.dart';
import '../map/map_view.dart';

/// Экран результата анализа: карта, объяснение ИИ и источники данных (T069).
class ResultScreen extends StatefulWidget {
  const ResultScreen({required this.runId, super.key});

  /// Идентификатор запуска.
  final String runId;

  @override
  State<ResultScreen> createState() => _ResultScreenState();
}

class _ResultScreenState extends State<ResultScreen> {
  ApiClient? _client;
  AnalysisResult? _result;
  String? _error;
  bool _loading = true;
  bool _started = false;

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    _client ??= ApiScope.of(context);
    if (!_started) {
      _started = true;
      unawaited(_load());
    }
  }

  Future<void> _load() async {
    final ApiClient? client = _client;
    if (client == null) {
      return;
    }
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final AnalysisResult result = await client.getRunResult(widget.runId);
      if (!mounted) {
        return;
      }
      setState(() {
        _result = result;
        _loading = false;
      });
    } on ApiException catch (error) {
      if (!mounted) {
        return;
      }
      setState(() {
        _error = error.message;
        _loading = false;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Результат анализа')),
      body: SafeArea(child: _buildBody(context)),
    );
  }

  Widget _buildBody(BuildContext context) {
    if (_loading) {
      return const Center(child: CircularProgressIndicator());
    }
    final String? error = _error;
    if (error != null) {
      return _buildError(error);
    }
    final AnalysisResult? result = _result;
    if (result == null) {
      return _buildError('Результат недоступен');
    }
    return _buildContent(context, result);
  }

  Widget _buildError(String message) {
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(24),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: <Widget>[
            const Icon(Icons.error_outline, size: 36, color: AppColors.lost),
            const SizedBox(height: 12),
            Text(
              message,
              textAlign: TextAlign.center,
              style: const TextStyle(color: AppColors.text),
            ),
            const SizedBox(height: 16),
            OutlinedButton(
              onPressed: () {
                unawaited(_load());
              },
              child: const Text('Повторить'),
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildContent(BuildContext context, AnalysisResult result) {
    return LayoutBuilder(
      builder: (BuildContext context, BoxConstraints constraints) {
        final bool wide = constraints.maxWidth >= 980;
        if (wide) {
          return Row(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: <Widget>[
              Expanded(child: MapView(result: result)),
              SizedBox(width: 400, child: _buildPanel(context, result)),
            ],
          );
        }
        return Column(
          children: <Widget>[
            SizedBox(height: 320, child: MapView(result: result)),
            Expanded(child: _buildPanel(context, result)),
          ],
        );
      },
    );
  }

  Widget _buildPanel(BuildContext context, AnalysisResult result) {
    final TextTheme textTheme = Theme.of(context).textTheme;
    final ChangeClassStats retained = result.aggregate.retained;
    final ChangeClassStats lost = result.aggregate.lost;
    final ChangeClassStats newZones = result.aggregate.newZones;
    final double total = retained.areaKm2 + lost.areaKm2 + newZones.areaKm2;
    final double? net = total > 0
        ? (newZones.areaKm2 - lost.areaKm2) / total * 100
        : null;
    final String netLabel = net == null
        ? '—'
        : '${net >= 0 ? '+' : ''}${_formatPercent(net)}%';
    final Color netColor = net == null
        ? AppColors.muted
        : net < 0
            ? AppColors.lost
            : AppColors.retained;
    final String explanation = result.explanationText.isNotEmpty
        ? result.explanationText
        : result.summaryText.isNotEmpty
            ? result.summaryText
            : 'Агенты сформировали результат анализа. Откройте источники '
                'данных, чтобы проверить исходные снимки.';
    return Container(
      decoration: const BoxDecoration(
        color: AppColors.surface,
        border: Border(left: BorderSide(color: AppColors.border)),
      ),
      child: SingleChildScrollView(
        padding: const EdgeInsets.all(24),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Row(
              children: <Widget>[
                Expanded(
                  child: Text(
                    'Статистика по территории',
                    style: textTheme.titleLarge?.copyWith(
                      fontWeight: FontWeight.w700,
                      color: AppColors.text,
                    ),
                  ),
                ),
                IconButton(
                  onPressed: () => Navigator.of(context).maybePop(),
                  icon: const Icon(Icons.close),
                  color: AppColors.muted,
                ),
              ],
            ),
            const SizedBox(height: 4),
            const Text(
              'Изменение растительности',
              style: TextStyle(color: AppColors.muted, fontSize: 13),
            ),
            const SizedBox(height: 24),
            const Text(
              'Общая динамика зелёных зон',
              style: TextStyle(color: AppColors.muted, fontSize: 13),
            ),
            const SizedBox(height: 6),
            Row(
              crossAxisAlignment: CrossAxisAlignment.end,
              children: <Widget>[
                Text(
                  netLabel,
                  style: TextStyle(
                    fontSize: 40,
                    fontWeight: FontWeight.w800,
                    color: netColor,
                    height: 1,
                  ),
                ),
                const SizedBox(width: 12),
                const Padding(
                  padding: EdgeInsets.only(bottom: 6),
                  child: Text(
                    'по площади классов',
                    style: TextStyle(color: AppColors.muted, fontSize: 12),
                  ),
                ),
              ],
            ),
            const SizedBox(height: 24),
            const Divider(height: 1, color: AppColors.border),
            const SizedBox(height: 16),
            _statRow(AppColors.lost, 'Потеряно', lost),
            _statRow(AppColors.retained, 'Сохранено (не изменение)', retained),
            _statRow(AppColors.newZone, 'Новые зоны', newZones),
            const SizedBox(height: 8),
            const Text(
              'Процент изменения = (новые − потерянные) / сумма площадей × 100%.',
              style: TextStyle(fontSize: 12, color: AppColors.muted),
            ),
            const SizedBox(height: 16),
            _qualityBox(result),
            const SizedBox(height: 20),
            _infoBox(explanation),
            if (result.rejections.isNotEmpty) ...<Widget>[
              const SizedBox(height: 20),
              _rejectionsBox(result),
            ],
            const SizedBox(height: 24),
            const Text(
              'Источники данных',
              style: TextStyle(
                fontSize: 14,
                fontWeight: FontWeight.w700,
                color: AppColors.text,
              ),
            ),
            const SizedBox(height: 12),
            _sourcesList(result),
            const SizedBox(height: 24),
            Wrap(
              spacing: 12,
              runSpacing: 12,
              children: <Widget>[
                SizedBox(
                  width: 160,
                  child: ExportButton(
                    runId: result.runId,
                    format: ExportFormat.pdf,
                  ),
                ),
                SizedBox(
                  width: 160,
                  child: ExportButton(
                    runId: result.runId,
                    format: ExportFormat.geojson,
                  ),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }

  String _formatPercent(double value) {
    final String text = value.abs() < 10
        ? value.toStringAsFixed(1)
        : value.toStringAsFixed(0);
    return text == '-0.0' ? '0.0' : text;
  }

  String _formatArea(double areaKm2) {
    if (areaKm2 == 0) {
      return '0';
    }
    if (areaKm2.abs() < 0.01) {
      return '${(areaKm2 * 1000000).round()} м²';
    }
    if (areaKm2.abs() < 1) {
      return '${areaKm2.toStringAsFixed(4)} км²';
    }
    return '${areaKm2.toStringAsFixed(2)} км²';
  }

  Widget _statRow(Color color, String label, ChangeClassStats stats) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 8),
      child: Row(
        children: <Widget>[
          Container(
            width: 10,
            height: 10,
            decoration: BoxDecoration(
              color: color,
              borderRadius: BorderRadius.circular(3),
            ),
          ),
          const SizedBox(width: 10),
          Expanded(
            child: Text(
              label,
              style: const TextStyle(fontSize: 14, color: AppColors.text),
            ),
          ),
          Column(
            crossAxisAlignment: CrossAxisAlignment.end,
            children: <Widget>[
              Text(
                _formatArea(stats.areaKm2),
                style: const TextStyle(
                  fontSize: 14,
                  fontWeight: FontWeight.w600,
                  color: AppColors.text,
                ),
              ),
              Text(
                '${stats.zonesCount} полиг.',
                style: const TextStyle(fontSize: 11, color: AppColors.muted),
              ),
            ],
          ),
        ],
      ),
    );
  }

  Widget _qualityBox(AnalysisResult result) {
    final double? shared = result.sharedCoverage;
    final Map<String, dynamic> dates =
        JsonUtils.asMap(result.diagnostics['actualDates']);
    final String before = JsonUtils.asList(dates['start'])
        .whereType<String>()
        .join(', ');
    final String after = JsonUtils.asList(dates['end'])
        .whereType<String>()
        .join(', ');
    final List<String> limitations = result.qualityLimitations;
    if (shared == null && before.isEmpty && after.isEmpty && limitations.isEmpty) {
      return const SizedBox.shrink();
    }
    return Container(
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: limitations.isEmpty
            ? AppColors.accentSoft
            : AppColors.warningBg,
        borderRadius: BorderRadius.circular(12),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Row(
            children: <Widget>[
              Icon(
                limitations.isEmpty
                    ? Icons.verified_outlined
                    : Icons.info_outline,
                size: 16,
                color: limitations.isEmpty
                    ? AppColors.primary
                    : AppColors.warningText,
              ),
              const SizedBox(width: 8),
              Text(
                'Достоверность данных',
                style: TextStyle(
                  fontSize: 13,
                  fontWeight: FontWeight.w700,
                  color: limitations.isEmpty
                      ? AppColors.primary
                      : AppColors.warningText,
                ),
              ),
            ],
          ),
          if (shared != null) ...<Widget>[
            const SizedBox(height: 6),
            Text(
              'Общее валидное покрытие обеих дат: '
              '${(shared * 100).toStringAsFixed(1)}%',
              style: const TextStyle(fontSize: 12, color: AppColors.text),
            ),
          ],
          if (before.isNotEmpty || after.isNotEmpty) ...<Widget>[
            const SizedBox(height: 4),
            Text(
              'Фактические даты: до ${before.isEmpty ? '—' : before}; '
              'после ${after.isEmpty ? '—' : after}.',
              style: const TextStyle(fontSize: 12, color: AppColors.text),
            ),
          ],
          for (final String limitation in limitations)
            Padding(
              padding: const EdgeInsets.only(top: 4),
              child: Text(
                '• $limitation',
                style: const TextStyle(
                  fontSize: 12,
                  color: AppColors.warningText,
                ),
              ),
            ),
          const SizedBox(height: 4),
          const Text(
            'Классы рассчитываются только на общем валидном покрытии: '
            'облака, тени, снег и NoData не считаются изменением.',
            style: TextStyle(fontSize: 11, color: AppColors.muted),
          ),
        ],
      ),
    );
  }

  Widget _infoBox(String text) {
    return Container(
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: AppColors.accentSoft,
        borderRadius: BorderRadius.circular(14),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          const Row(
            children: <Widget>[
              Icon(Icons.auto_awesome, size: 16, color: AppColors.primary),
              SizedBox(width: 8),
              Text(
                'Что это значит?',
                style: TextStyle(
                  fontSize: 13,
                  fontWeight: FontWeight.w700,
                  color: AppColors.primary,
                ),
              ),
            ],
          ),
          const SizedBox(height: 8),
          Text(
            text,
            style: const TextStyle(
              fontSize: 13,
              height: 1.5,
              color: AppColors.text,
            ),
          ),
        ],
      ),
    );
  }

  Widget _rejectionsBox(AnalysisResult result) {
    final List<Widget> items = <Widget>[];
    for (final ValidationRejection rejection in result.rejections) {
      for (final String reason in rejection.reasons) {
        items.add(
          Padding(
            padding: const EdgeInsets.only(bottom: 4),
            child: Text(
              '• $reason',
              style: const TextStyle(
                fontSize: 13,
                color: AppColors.warningText,
              ),
            ),
          ),
        );
      }
    }
    return Container(
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: AppColors.warningBg,
        borderRadius: BorderRadius.circular(14),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          const Row(
            children: <Widget>[
              Icon(
                Icons.report_outlined,
                size: 16,
                color: AppColors.warningText,
              ),
              SizedBox(width: 8),
              Text(
                'Замечания валидатора',
                style: TextStyle(
                  fontSize: 13,
                  fontWeight: FontWeight.w700,
                  color: AppColors.warningText,
                ),
              ),
            ],
          ),
          const SizedBox(height: 8),
          ...items,
        ],
      ),
    );
  }

  Widget _sourcesList(AnalysisResult result) {
    if (result.sources.isEmpty) {
      return const Text(
        'Источники не указаны',
        style: TextStyle(fontSize: 13, color: AppColors.muted),
      );
    }
    return Column(
      children: result.sources
          .map(
            (SourceRef source) => Container(
              margin: const EdgeInsets.only(bottom: 10),
              padding: const EdgeInsets.all(12),
              decoration: BoxDecoration(
                borderRadius: BorderRadius.circular(12),
                border: Border.all(color: AppColors.border),
              ),
              child: Row(
                children: <Widget>[
                  Container(
                    width: 36,
                    height: 36,
                    decoration: BoxDecoration(
                      color: AppColors.accentSoft,
                      borderRadius: BorderRadius.circular(10),
                    ),
                    child: const Icon(
                      Icons.public,
                      size: 18,
                      color: AppColors.primary,
                    ),
                  ),
                  const SizedBox(width: 12),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: <Widget>[
                        Text(
                          source.source.isNotEmpty
                              ? source.source
                              : 'Источник',
                          style: const TextStyle(
                            fontSize: 13,
                            fontWeight: FontWeight.w600,
                            color: AppColors.text,
                          ),
                        ),
                        const SizedBox(height: 2),
                        Text(
                          _sourceSubtitle(source),
                          style: const TextStyle(
                            fontSize: 12,
                            color: AppColors.muted,
                          ),
                        ),
                      ],
                    ),
                  ),
                ],
              ),
            ),
          )
          .toList(growable: false),
    );
  }

  String _sourceSubtitle(SourceRef source) {
    final List<String> parts = <String>[];
    if (source.collection.isNotEmpty) {
      parts.add(source.collection);
    }
    final DateTime? acquired = source.acquiredAt;
    if (acquired != null) {
      parts.add(
        '${_twoDigits(acquired.day)}.${_twoDigits(acquired.month)}.'
        '${acquired.year}',
      );
    }
    final double? resolution = source.resolutionM;
    if (resolution != null) {
      parts.add('${resolution.toStringAsFixed(0)} м');
    }
    return parts.isEmpty
        ? 'Данные дистанционного зондирования'
        : parts.join(' · ');
  }

  String _twoDigits(int value) => value < 10 ? '0$value' : '$value';
}
