import QtQuick

// Карточка-подложка: фон поверхности, скруглённые углы и тонкая рамка из токенов темы (Theme).
Rectangle {
    color: Theme.surface
    radius: Theme.radiusL
    border.width: 1
    border.color: Theme.border
}
