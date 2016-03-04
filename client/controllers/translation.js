angular.module('mecab-translate')
.controller('Translation', function($scope, $sce, Edict2, JMdict_e, Helpers) {

    $scope.isCommon = function(word) {
        return Helpers.intersection(Helpers.commonPriority, word.pri).length;
    }

    Edict2.setOutput(function(output) {
        $scope.entries = output.sort(Helpers.commonSort);
    });

    JMdict_e.setOutput(function(output) {
        $scope.jmdicEntries = output.sort(function(a, b) {

            [a, b].forEach(function(entry) {
                var common = false;
                for (var i in entry.words) {
                    if ($scope.isCommon(entry.words[i])) {
                        common = true;
                        break;
                    }
                }
                for (var i in entry.readings) {
                    if ($scope.isCommon(entry.readings[i])) {
                        common = true;
                        break;
                    }
                }
                entry.common = common;
            });

            return Helpers.commonSort(a, b);

        });
    });

    $scope.translate = function(text) {
        JMdict_e.translate(text);
    }

    $scope.parseReference = function(ref) {
        return ref.split('・')[0];
    }

    $scope.w = function(text) {
        return $sce.trustAsHtml(Helpers.parenHilite(Helpers.color(text, 'red')));
    }

    $scope.r = function(text) {
        return $sce.trustAsHtml(Helpers.parenHilite(Helpers.color(text, 'green')));
    }

    $scope.d = function(text) {
        return $sce.trustAsHtml(Helpers.parenHilite(text));
    }

});
