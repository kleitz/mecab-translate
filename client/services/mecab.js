angular.module('mecab-translate')
.factory('Mecab', function($http) {

    var history = [];
    var historyIndex = -1;

    return {
        analyze: function(val, analyzingHistory) {
            if (!analyzingHistory && val && val.length) {
                if (history.length >= 50) {
                    history.shift(1);
                    historyIndex--;
                }
                // this is the browser-like behavior but probably not wanted here
                //if (historyIndex > -1)
                //    history.length = historyIndex + 1;
                history.push(val);
                historyIndex = history.length - 1;
            }
            if (historyIndex == 0) {
                this.back.disabled = true;
            } else {
                this.back.disabled = false;
            }
            if (historyIndex == history.length - 1) {
                this.forward.disabled = true;
            } else {
                this.forward.disabled = false;
            }
            $http.post('/mecab', angular.toJson(val))
            .then(function success(data) {
                this.output(data.data);
            }.bind(this), function error(data) {
                this.output([]);
            }.bind(this));
        },
        setOutput: function(fn) {
            this.output = fn;
        },
        analyzeHistory: function(offset) {
            var newValue = historyIndex + offset;
            if (newValue > -1 && newValue < history.length) {
                historyIndex = newValue;
                this.analyze(history[historyIndex], true);
                this.input.value = history[historyIndex];
            }
        },
        setInput: function(textarea) {
            this.input = textarea;
        },
        setButtons: function(back, forward) {
            this.back = back;
            this.forward = forward;
        }
    }

});
