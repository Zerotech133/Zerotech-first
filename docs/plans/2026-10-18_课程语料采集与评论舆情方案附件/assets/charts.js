// 课程语料采集与评论舆情方案 · 图表逻辑
(function() {
  var style = getComputedStyle(document.documentElement);
  var accent = style.getPropertyValue('--accent').trim();
  var accent2 = style.getPropertyValue('--accent2').trim();
  var ink = style.getPropertyValue('--ink').trim();
  var muted = style.getPropertyValue('--muted').trim();
  var rule = style.getPropertyValue('--rule').trim();
  var bg2 = style.getPropertyValue('--bg2').trim();

  // --- Chart: 新增海外课程候选域名分组数量 ---
  var chart = echarts.init(document.getElementById('chart-rules'), null, { renderer: 'svg' });
  chart.setOption({
    animation: false,
    tooltip: { trigger: 'axis', appendToBody: true },
    grid: { left: 48, right: 24, top: 24, bottom: 40 },
    xAxis: {
      type: 'category',
      data: ['YouTube 系', 'X 媒体', '垂直教育/理论', '财经理论/机构'],
      axisLine: { lineStyle: { color: rule } },
      axisLabel: { color: ink, fontFamily: 'BricolageGrotesque' },
      axisTick: { show: false }
    },
    yAxis: {
      type: 'value',
      minInterval: 1,
      axisLine: { show: false },
      splitLine: { lineStyle: { color: rule } },
      axisLabel: { color: muted, fontFamily: 'JetBrainsMono' }
    },
    series: [{
      type: 'bar',
      data: [2, 2, 4, 2],
      barWidth: 46,
      itemStyle: {
        color: function(params) {
          return params.dataIndex === 2 ? accent2 : accent;
        },
        borderRadius: [6, 6, 0, 0]
      },
      label: {
        show: true,
        position: 'top',
        color: ink,
        fontFamily: 'JetBrainsMono',
        fontWeight: 700
      }
    }]
  });
  window.addEventListener('resize', function() { chart.resize(); });
})();
