/* 缠论学习环运行报告 · ECharts 图表（数据取自 2026-10-18 P0 落盘产物） */
(function () {
  var INK = "#e6edf3", MUTED = "#8b949e", RULE = "#30363d";
  var ACC = "#58a6ff", ACC2 = "#e3b341", POS = "#3fb950", NEG = "#f85149";
  var FONT = '"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif';
  var MONO = '"Cascadia Code",Consolas,monospace';

  var base = {
    textStyle: { color: INK, fontFamily: FONT },
    grid: { left: 56, right: 28, top: 40, bottom: 44, containLabel: true },
    tooltip: {
      backgroundColor: "#161b22", borderColor: RULE, textStyle: { color: INK },
      confine: true
    }
  };
  function axisX(data) {
    return {
      type: "category", data: data,
      axisLine: { lineStyle: { color: RULE } }, axisTick: { show: false },
      axisLabel: { color: MUTED, fontSize: 12 }
    };
  }
  function axisY(name, fmt) {
    return {
      type: "value", name: name, nameTextStyle: { color: MUTED, fontSize: 12 },
      axisLine: { show: false }, axisTick: { show: false },
      splitLine: { lineStyle: { color: "#21262d" } },
      axisLabel: { color: MUTED, fontSize: 12, formatter: fmt || "{value}" }
    };
  }

  var charts = [];

  /* 图1 信号类型分布 */
  var el1 = document.getElementById("chartType");
  if (el1) {
    var c1 = echarts.init(el1);
    c1.setOption(Object.assign({}, base, {
      xAxis: axisX(["1买", "2买", "3买", "1卖", "2卖", "3卖"]),
      yAxis: [axisY("信号数")],
      series: [{
        type: "bar", barWidth: 42,
        data: [
          { value: 36, itemStyle: { color: POS } },
          { value: 44, itemStyle: { color: POS } },
          { value: 21, itemStyle: { color: POS } },
          { value: 26, itemStyle: { color: NEG } },
          { value: 28, itemStyle: { color: NEG } },
          { value: 52, itemStyle: { color: NEG } }
        ],
        label: { show: true, position: "top", color: INK, fontFamily: MONO, fontWeight: 600 }
      }]
    }));
    charts.push(c1);
  }

  /* 图2 月度趋势 */
  var el2 = document.getElementById("chartMonthly");
  if (el2) {
    var c2 = echarts.init(el2);
    var months = ["25-10", "25-11", "25-12", "26-01", "26-02", "26-03", "26-04", "26-05", "26-06", "26-07", "26-08", "26-09", "26-10*"];
    var mc = [29, 29, 23, 18, 12, 5, 22, 9, 6, 13, 12, 9, 20];
    var cum = [], s = 0;
    mc.forEach(function (v) { s += v; cum.push(s); });
    c2.setOption(Object.assign({}, base, {
      legend: { data: ["当月信号", "累计"], textStyle: { color: MUTED }, top: 4 },
      xAxis: axisX(months),
      yAxis: [
        axisY("当月信号"),
        Object.assign(axisY("累计"), { splitLine: { show: false } })
      ],
      series: [
        { name: "当月信号", type: "bar", barWidth: 20, data: mc, itemStyle: { color: ACC },
          label: { show: true, position: "top", color: MUTED, fontSize: 11, fontFamily: MONO } },
        { name: "累计", type: "line", yAxisIndex: 1, data: cum, itemStyle: { color: ACC2 }, lineStyle: { width: 2 }, symbolSize: 5 }
      ]
    }));
    charts.push(c2);
  }

  /* 图3 买卖方向胜率 */
  var el3 = document.getElementById("chartDir");
  if (el3) {
    var c3 = echarts.init(el3);
    c3.setOption(Object.assign({}, base, {
      legend: { data: ["买侧", "卖侧"], textStyle: { color: MUTED }, top: 4 },
      xAxis: axisX(["T+5", "T+10 主口径", "T+20"]),
      yAxis: [axisY("win %", "{value}%")],
      series: [
        { name: "买侧", type: "bar", barWidth: 26, data: [95.0, 90.1, 77.0], itemStyle: { color: POS },
          label: { show: true, position: "top", color: INK, fontFamily: MONO, formatter: "{c}%" } },
        { name: "卖侧", type: "bar", barWidth: 26, data: [93.4, 81.1, 71.4], itemStyle: { color: NEG },
          label: { show: true, position: "top", color: INK, fontFamily: MONO, formatter: "{c}%" } }
      ]
    }));
    charts.push(c3);
  }

  /* 图4 分类型 T+10 胜率（横向） */
  var el4 = document.getElementById("chartTypeWin");
  if (el4) {
    var c4 = echarts.init(el4);
    var rows = [
      ["1买", 100.0, 36, POS], ["1卖", 96.2, 26, NEG], ["2买", 93.2, 44, POS],
      ["2卖", 82.1, 28, NEG], ["3卖", 73.1, 52, NEG], ["3买", 66.7, 21, POS]
    ];
    c4.setOption(Object.assign({}, base, {
      grid: { left: 56, right: 90, top: 20, bottom: 40, containLabel: true },
      xAxis: [{ type: "value", max: 105, name: "win %", nameTextStyle: { color: MUTED },
        axisLine: { show: false }, axisTick: { show: false },
        splitLine: { lineStyle: { color: "#21262d" } },
        axisLabel: { color: MUTED, formatter: "{value}%" } }],
      yAxis: [{
        type: "category", data: rows.map(function (r) { return r[0]; }).reverse(),
        axisLine: { lineStyle: { color: RULE } }, axisTick: { show: false },
        axisLabel: { color: INK, fontSize: 13, fontWeight: 600 }
      }],
      series: [{
        type: "bar", barWidth: 20,
        data: rows.slice().reverse().map(function (r) {
          return { value: r[1], itemStyle: { color: r[3] },
            label: { show: true, position: "right", color: INK, fontFamily: MONO,
              formatter: r[1].toFixed(1) + "% (n=" + r[2] + ")" } };
        })
      }]
    }));
    charts.push(c4);
  }

  /* 图5 T+10 收益直方图 */
  var el5 = document.getElementById("chartHist");
  if (el5) {
    var c5 = echarts.init(el5);
    var bins = ["<-10", "-10~-5", "-5~-2", "-2~0", "0~2", "2~5", "5~10", "10~20", ">=20"];
    var cnt = [0, 3, 11, 16, 34, 48, 58, 31, 6];
    c5.setOption(Object.assign({}, base, {
      xAxis: axisX(bins),
      yAxis: [axisY("信号数")],
      series: [{
        type: "bar", barWidth: 26,
        data: cnt.map(function (v, i) {
          return { value: v, itemStyle: { color: i <= 3 ? NEG : (i >= 4 ? POS : MUTED) } };
        }),
        label: { show: true, position: "top", color: INK, fontFamily: MONO }
      }]
    }));
    charts.push(c5);
  }

  /* 图6 T+10 verdict 分布（环形） */
  var el6 = document.getElementById("chartVerdict");
  if (el6) {
    var c6 = echarts.init(el6);
    c6.setOption({
      textStyle: { color: INK, fontFamily: FONT },
      tooltip: { backgroundColor: "#161b22", borderColor: RULE, textStyle: { color: INK }, confine: true },
      legend: { bottom: 6, textStyle: { color: MUTED } },
      series: [{
        type: "pie", radius: ["42%", "66%"], center: ["50%", "44%"],
        label: { color: INK, formatter: "{b}\n{c} ({d}%)" },
        labelLine: { lineStyle: { color: RULE } },
        data: [
          { value: 153, name: "只观察 (n<30)", itemStyle: { color: "#30363d" } },
          { value: 23, name: "正常", itemStyle: { color: ACC } },
          { value: 5, name: "升置信候选", itemStyle: { color: POS } }
        ]
      }]
    });
    charts.push(c6);
  }

  window.addEventListener("resize", function () {
    charts.forEach(function (c) { c.resize(); });
  });
})();
