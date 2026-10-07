package com.qianjiangsoft.graphapi.controller;

import com.qianjiangsoft.graphapi.constants.AnalysisFunctionType;
import com.qianjiangsoft.graphapi.aop.annotation.SearchHistory;
import com.qianjiangsoft.graphapi.dto.*;
import com.qianjiangsoft.graphapi.param.*;
import com.qianjiangsoft.graphapi.service.AnalysisService;
import com.qianjiangsoft.graphapi.service.DataService;
import com.qianjiangsoft.graphcore.param.TextSearchParam;
import io.swagger.annotations.ApiOperation;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.*;

import java.util.List;

/**
 * 图谱分析
 *
 * @author ulmarr8861
 * @date 2022/4/8
 */
@RestController
@RequestMapping("/analysis")
@RequiredArgsConstructor
public class GraphAnalysisController {

    private final DataService dataService;

    private final AnalysisService analysisService;


    /**
     * 图谱分析-查找子图
     */
    @PostMapping("/graph/findSub")
    @SearchHistory(type = AnalysisFunctionType.FIND_SUB)
    public GraphCommonReturnDTO findSubGraph(@RequestBody SearchSubGraphParam param) {

        return analysisService.searchSubgraph(param);
    }

    /**
     * 图谱分析-图谱扩展
     */
    @PostMapping("/graph/extend")
    public GraphCommonReturnDTO extendGraph(@RequestBody GraphExtendParam param) {
        return analysisService.extendGraph(param);
    }

    /**
     * 图谱分析-查找路径
     */
    @PostMapping("/graph/findPath")
    @SearchHistory(type = AnalysisFunctionType.FIND_PATH)
    public GraphPathsReturnDTO findPathGraph(@RequestBody GraphExploreParam param) {

        return analysisService.graphFindPath(param);
    }

    /**
     * 图谱分析-查找回路
     */
    @PostMapping("/graph/findLoop")
    @SearchHistory(type = AnalysisFunctionType.FIND_LOOP)
    public GraphPathsReturnDTO findLoopGraph(@RequestBody GraphFindLoopParam param) {
        return analysisService.graphFindLoop(param);
    }

    /**
     * 图谱分析-复杂查询
     */
    @PostMapping("/graph/complexSearch")
    @SearchHistory(type = AnalysisFunctionType.COMPLEX)
    public GraphCommonReturnDTO complexSearch(@RequestBody GraphComplexSearch param) {
        return analysisService.complexSearch(param);
    }

    /**
     * 图谱分析-图谱统计
     */
    @PostMapping("/graph/statistics")
    @SearchHistory(type = AnalysisFunctionType.STATISTICS)
    public GraphCommonReturnDTO graphStatistics(@RequestBody GraphStatisticsParam param) {
        return analysisService.graphStatistics(param);
    }


    /**
     * 图谱分析-实体查找-Id
     */
    @GetMapping("/vertex/findById")
    public VertexReturnDTO findVertexById(@RequestParam("id") String id) {
        return dataService.findVertexById(id);
    }

    /**
     * 图谱分析-实体查找-name
     */
    @PostMapping("/vertex/searchByName")
    @SearchHistory(type = AnalysisFunctionType.GENERAL)
    public List<VertexReturnDTO> searchVertexListByName(@RequestBody searchVertexListByNameParam param) {

        return analysisService.searchVertexListByName(param);
    }

    /**
     * 图谱分析-查找文本
     */
    @PostMapping("/text/search")
    @SearchHistory(type = AnalysisFunctionType.TEXT)
    public GraphCommonReturnDTO textSearch(@RequestBody TextSearchParam param) {
        return analysisService.textSearch(param);
    }

    /**
     * 图谱分析-资金流向
     */
    @PostMapping("/graph/flowDirection")
    @SearchHistory(type = AnalysisFunctionType.FLAW_DIRECTION)
    public GraphCommonReturnDTO flowDirection(@RequestBody FlowDirectionParam flowDirectionParam) {

        return analysisService.analyseFlowDirection(flowDirectionParam);
    }

    /**
     * 图谱分析-通过MergeEdge查询Edge
     */
    @PostMapping("/edge/queryByMergeEdge")
    public List<EdgeReturnDTO> queryEdgeByMergeEdge(@RequestBody MergeEdgeConvertParam param) {
        return analysisService.queryEdgeByMergeEdge(param);
    }

    /**
     * 图谱分析-原始数据列表导出excel
     */
    @ApiOperation(value = "原始数据列表导出excel")
    @PostMapping("/exportExcel")
    public void exportExcel(@RequestBody MergeEdgeConvertParam param) {
        analysisService.exportExcel(param);
    }

    /**
     * 图谱分析-查找文本
     */
    @PostMapping("/graph/searchSameTagRelation")
    @SearchHistory(type = AnalysisFunctionType.SearchSameTagRelation)
    public GraphCommonReturnDTO searchSameTagRelation(@RequestBody SearchSameTagRelationParam param) {
        return analysisService.searchSameTagRelation(param);
    }
}



