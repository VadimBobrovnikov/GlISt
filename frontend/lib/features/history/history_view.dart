import 'dart:async';

import 'package:flutter/material.dart';

import '../../core/api_client.dart';
import '../../core/api_provider.dart';
import '../../core/app_theme.dart';
import '../../shared/models/models.dart';
import '../progress/progress_view.dart';
import '../result/result_view.dart';

/// История запросов пользователя с переходом к соответствующему запуску.
class HistoryScreen extends StatefulWidget {
  const HistoryScreen({super.key});

  @override
  State<HistoryScreen> createState() => _HistoryScreenState();
}

class _HistoryScreenState extends State<HistoryScreen> {
  ApiClient? _client;
  List<QuerySummary> _items = <QuerySummary>[];
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
      final QueryPage page = await client.listQueries(limit: 50);
      if (!mounted) {
        return;
      }
      setState(() {
        _items = page.items;
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

  void _open(QuerySummary item) {
    final String? runId = item.lastRunId;
    if (runId == null) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('Для запроса ещё нет запуска')),
      );
      return;
    }
    final bool ready = item.status == RunStatus.completed ||
        item.status == RunStatus.validationFailed;
    Navigator.of(context).push(
      MaterialPageRoute<void>(
        builder: (_) => ready
            ? ResultScreen(runId: runId)
            : ProgressScreen(runId: runId, queryText: item.text),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('История анализов')),
      body: SafeArea(child: _buildBody()),
    );
  }

  Widget _buildBody() {
    if (_loading) {
      return const Center(child: CircularProgressIndicator());
    }
    final String? error = _error;
    if (error != null) {
      return Center(
        child: Padding(
          padding: const EdgeInsets.all(24),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: <Widget>[
              const Icon(Icons.error_outline, color: AppColors.lost, size: 36),
              const SizedBox(height: 12),
              Text(error, textAlign: TextAlign.center),
              const SizedBox(height: 12),
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
    if (_items.isEmpty) {
      return const Center(
        child: Text(
          'Пока нет запросов',
          style: TextStyle(color: AppColors.muted),
        ),
      );
    }
    return RefreshIndicator(
      onRefresh: _load,
      child: ListView.separated(
        padding: const EdgeInsets.all(16),
        itemCount: _items.length,
        separatorBuilder: (BuildContext context, int index) =>
            const SizedBox(height: 10),
        itemBuilder: (BuildContext context, int index) {
          final QuerySummary item = _items[index];
          return Material(
            color: AppColors.surface,
            borderRadius: BorderRadius.circular(14),
            child: InkWell(
              onTap: () => _open(item),
              borderRadius: BorderRadius.circular(14),
              child: Container(
                padding: const EdgeInsets.all(16),
                decoration: BoxDecoration(
                  borderRadius: BorderRadius.circular(14),
                  border: Border.all(color: AppColors.border),
                ),
                child: Row(
                  children: <Widget>[
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: <Widget>[
                          Text(
                            item.text,
                            maxLines: 2,
                            overflow: TextOverflow.ellipsis,
                            style: const TextStyle(
                              fontSize: 14,
                              fontWeight: FontWeight.w600,
                              color: AppColors.text,
                            ),
                          ),
                          const SizedBox(height: 6),
                          Text(
                            '${item.analysisType.label} · '
                            '${_formatDate(item.createdAt)}',
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                            style: const TextStyle(
                              fontSize: 12,
                              color: AppColors.muted,
                            ),
                          ),
                        ],
                      ),
                    ),
                    const SizedBox(width: 12),
                    _statusBadge(item.status),
                    const SizedBox(width: 8),
                    const Icon(
                      Icons.arrow_forward,
                      size: 16,
                      color: AppColors.muted,
                    ),
                  ],
                ),
              ),
            ),
          );
        },
      ),
    );
  }

  Widget _statusBadge(RunStatus status) {
    final (String label, Color background, Color foreground) = switch (status) {
      RunStatus.completed => (
          'Завершено',
          AppColors.successBg,
          AppColors.successText,
        ),
      RunStatus.validationFailed => (
          'Не прошёл проверку',
          AppColors.warningBg,
          AppColors.warningText,
        ),
      RunStatus.noData => (
          'Нет данных',
          AppColors.warningBg,
          AppColors.warningText,
        ),
      RunStatus.error => (
          'Ошибка',
          AppColors.errorBg,
          AppColors.errorText,
        ),
      RunStatus.processing => (
          'В обработке',
          AppColors.accentSoft,
          AppColors.primary,
        ),
      RunStatus.waiting => (
          'Ожидает',
          AppColors.accentSoft,
          AppColors.primary,
        ),
      RunStatus.unknown => (
          'Неизвестно',
          AppColors.border,
          AppColors.muted,
        ),
    };
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
      decoration: BoxDecoration(
        color: background,
        borderRadius: BorderRadius.circular(20),
      ),
      child: Text(
        label,
        style: TextStyle(
          fontSize: 11,
          fontWeight: FontWeight.w600,
          color: foreground,
        ),
      ),
    );
  }

  String _formatDate(DateTime? value) {
    if (value == null) {
      return 'Дата неизвестна';
    }
    final DateTime local = value.toLocal();
    String two(int number) => number < 10 ? '0$number' : '$number';
    return '${two(local.day)}.${two(local.month)}.${local.year} '
        '${two(local.hour)}:${two(local.minute)}';
  }
}
