package com.qianjiangsoft.graphapi.controller;

import com.qianjiangsoft.graphapi.dto.QueryGraphAnalyseTaskListDTO;
import com.qianjiangsoft.graphapi.param.analysisTask.SearchTaskParam;
import com.qianjiangsoft.graphapi.service.AnalysisTaskService;
import com.qianjiangsoft.graphcore.page.PageDataVO;
import io.swagger.annotations.ApiOperation;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.*;

/**
 * 图谱分析
 *
 * @author ulmarr8861
 * @date 2022/4/8
 */
@RestController
@RequestMapping("/analysis/task/")
@RequiredArgsConstructor
public class AnalysisTaskController {

    private final AnalysisTaskService analysisTaskService;

    /**
     * 搜索历史-搜索记录查询
     */
    @ApiOperation(value = "搜索任务列表")
    @GetMapping("/queryListByPage")
    public PageDataVO<QueryGraphAnalyseTaskListDTO> queryListByPage(SearchTaskParam param) {
        return analysisTaskService.queryListByParam(param);
    }

    /**
     * 搜索历史-删除搜索记录
     */
    @ApiOperation(value = "删除分析任务")
    @PostMapping("/delete")
    public void delete(@RequestParam Integer id) {
        analysisTaskService.deleteTask(id);
    }

    /**
     * 搜索历史-搜索记录查询
     */
    @ApiOperation(value = "查询任务结果")
    @GetMapping("/queryTaskResult")
    public Object queryTaskResult(Integer id) {
        return analysisTaskService.queryTaskResult(id);
    }
}



