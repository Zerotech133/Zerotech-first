package com.qianjiangsoft.graphapi.controller;

import com.qianjiangsoft.graphapi.dto.GraphCommonReturnDTO;
import com.qianjiangsoft.graphapi.param.custom.QueryCrossCaseUnknownSuspectParam;
import com.qianjiangsoft.graphapi.service.AnalysisCustomService;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.*;

/**
 * 图谱分析
 *
 * @author ulmarr8861
 * @date 2022/4/8
 */
@RestController
@RequestMapping("/analysis/custom")
@RequiredArgsConstructor
public class GraphAnalysisCustomAPIController {

    private final AnalysisCustomService analysisCustomService;



    /**
     * 跨案件位置嫌疑人查询
     */
    @GetMapping("/queryCrossCaseUnknownSuspect")
    public GraphCommonReturnDTO queryCrossCaseUnknownSuspect() {
        return analysisCustomService.queryCrossCaseUnknownSuspect();
    }

}



