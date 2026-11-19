import 'package:flutter/material.dart';

/// Дизайн-токены GlISt из `docs/ui-design-system.md`.
class AppColors {
  const AppColors._();

  /// Фон приложения.
  static const Color background = Color(0xFFF8FAFC);

  /// Основной акцент (кнопки, ссылки).
  static const Color primary = Color(0xFF2563EB);

  /// Основной текст.
  static const Color text = Color(0xFF0F172A);

  /// Приглушённый текст.
  static const Color muted = Color(0xFF64748B);

  /// Поверхность карточек.
  static const Color surface = Colors.white;

  /// Границы карточек.
  static const Color border = Color(0xFFE2E8F0);

  /// Сохранившиеся зоны (зелёный).
  static const Color retained = Color(0xFF22C55E);

  /// Потерянные зоны (красный).
  static const Color lost = Color(0xFFEF4444);

  /// Новые зоны (жёлтый).
  static const Color newZone = Color(0xFFFACC15);

  /// Затопление: постоянная вода (синий).
  static const Color waterPermanent = Color(0xFF2563EB);

  /// Затопление: вода ушла в базовый период (охра).
  static const Color waterReceded = Color(0xFFB45309);

  /// Затопление: временное затопление (голубой).
  static const Color waterTemporary = Color(0xFF06B6D4);

  /// Застройка: сохранилась (серый).
  static const Color builtRetained = Color(0xFF6B7280);

  /// Застройка: исчезла (коричневый).
  static const Color builtLost = Color(0xFF92400E);

  /// Новая застройка (оранжевый).
  static const Color builtNew = Color(0xFFF97316);

  /// Фон бейджа «Завершено».
  static const Color successBg = Color(0xFFDCFCE7);

  /// Текст бейджа «Завершено».
  static const Color successText = Color(0xFF166534);

  /// Фон бейджа «Не прошёл проверку».
  static const Color warningBg = Color(0xFFFEF3C7);

  /// Текст бейджа «Не прошёл проверку».
  static const Color warningText = Color(0xFF92400E);

  /// Фон бейджа «Ошибка».
  static const Color errorBg = Color(0xFFFEE2E2);

  /// Текст бейджа «Ошибка».
  static const Color errorText = Color(0xFF991B1B);

  /// Мягкий акцентный фон (подсказки, пояснения).
  static const Color accentSoft = Color(0xFFEFF6FF);
}

/// Собирает [ThemeData] приложения из дизайн-токенов.
ThemeData buildGlistTheme() {
  final ColorScheme scheme = ColorScheme.fromSeed(
    seedColor: AppColors.primary,
    primary: AppColors.primary,
    surface: AppColors.surface,
  );
  return ThemeData(
    useMaterial3: true,
    colorScheme: scheme,
    scaffoldBackgroundColor: AppColors.background,
    appBarTheme: const AppBarTheme(
      backgroundColor: AppColors.surface,
      surfaceTintColor: Colors.transparent,
      foregroundColor: AppColors.text,
      elevation: 0,
      centerTitle: false,
    ),
    filledButtonTheme: FilledButtonThemeData(
      style: FilledButton.styleFrom(
        backgroundColor: AppColors.primary,
        foregroundColor: Colors.white,
        minimumSize: const Size.fromHeight(52),
        textStyle: const TextStyle(fontSize: 16, fontWeight: FontWeight.w600),
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(12),
        ),
      ),
    ),
    inputDecorationTheme: InputDecorationTheme(
      filled: true,
      fillColor: AppColors.surface,
      contentPadding: const EdgeInsets.all(16),
      hintStyle: const TextStyle(color: AppColors.muted),
      border: OutlineInputBorder(
        borderRadius: BorderRadius.circular(12),
        borderSide: const BorderSide(color: AppColors.border),
      ),
      enabledBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(12),
        borderSide: const BorderSide(color: AppColors.border),
      ),
      focusedBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(12),
        borderSide: const BorderSide(color: AppColors.primary, width: 1.5),
      ),
    ),
    snackBarTheme: const SnackBarThemeData(
      behavior: SnackBarBehavior.floating,
    ),
  );
}
