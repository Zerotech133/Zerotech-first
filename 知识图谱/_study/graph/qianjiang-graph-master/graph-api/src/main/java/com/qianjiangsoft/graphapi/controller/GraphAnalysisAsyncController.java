package com.qianjiangsoft.graphapi.controller;

import com.qianjiangsoft.graphapi.aop.annotation.AsyncAnalysisTask;
import com.qianjiangsoft.graphapi.constants.AnalysisFunctionType;
import com.qianjiangsoft.graphapi.aop.annotation.SearchHistory;
import com.qianjiangsoft.graphapi.dto.GraphCommonReturnDTO;
import com.qianjiangsoft.graphapi.dto.GraphPathsReturnDTO;
import com.qianjiangsoft.graphapi.param.*;
import com.qianjiangsoft.graphapi.service.AnalysisService;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.*;

/**
 * 图谱分析
 *
 * @author ulmarr8861
 * @date 2022/4/8
 */
@RestController
@RequestMapping("/analysis/async")
@RequiredArgsConstructor
public class GraphAnalysisAsyncController {

    private final AnalysisService analysisService;


    /**
     * 图谱分析-查找子图
     */
    @PostMapping("/findSub")
    @SearchHistory(type = AnalysisFunctionType.FIND_SUB)
    @AsyncAnalysisTask(type = AnalysisFunctionType.FIND_SUB)
    public GraphCommonReturnDTO findSubGraph(@RequestBody SearchSubGraphParam param) {

        return analysisService.searchSubgraph(param);
    }

    /**
     * 图谱分析-查找路径
     */
    @PostMapping("/findPath")
    @SearchHistory(type = AnalysisFunctionType.FIND_PATH)
    @AsyncAnalysisTask(type = AnalysisFunctionType.FIND_PATH)
    public GraphPathsReturnDTO findPathGraph(@RequestBody GraphExploreParam param) {

        return analysisService.graphFindPath(param);
    }

    /**
     * 图谱分析-查找回路
     */
    @PostMapping("/findLoop")
    @SearchHistory(type = AnalysisFunctionType.FIND_LOOP)
    @AsyncAnalysisTask(type = AnalysisFunctionType.FIND_LOOP)
    public GraphPathsReturnDTO findLoopGraph(@RequestBody GraphFindLoopParam param) {
        return analysisService.graphFindLoop(param);
    }


    /**
     * 图谱分析-资金流向
     */
    @PostMapping("/flowDirection")
    @SearchHistory(type = AnalysisFunctionType.FLAW_DIRECTION)
    @AsyncAnalysisTask(type = AnalysisFunctionType.FLAW_DIRECTION)
    public GraphCommonReturnDTO flowDirection(@RequestBody FlowDirectionParam flowDirectionParam) {
        return analysisService.analyseFlowDirection(flowDirectionParam);
    }

    /**
     * 图谱分析-查找文本
     */
    @PostMapping("/searchSameTagRelation")
    @SearchHistory(type = AnalysisFunctionType.SearchSameTagRelation)
    @AsyncAnalysisTask(type = AnalysisFunctionType.SearchSameTagRelation)
    public GraphCommonReturnDTO searchSameTagRelation(@RequestBody SearchSameTagRelationParam param) {
        return analysisService.searchSameTagRelation(param);
    }


}



