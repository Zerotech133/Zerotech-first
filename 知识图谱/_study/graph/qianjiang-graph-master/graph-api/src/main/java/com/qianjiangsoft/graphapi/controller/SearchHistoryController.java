package com.qianjiangsoft.graphapi.controller;

import com.qianjiangsoft.graphapi.dto.*;
import com.qianjiangsoft.graphapi.param.history.SearchHistoryParam;
import com.qianjiangsoft.graphapi.service.SearchHistoryService;
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
@RequestMapping("/searchHistory")
@RequiredArgsConstructor
public class SearchHistoryController {

    private final SearchHistoryService searchHistoryService;

    /**
     * 搜索历史-搜索记录查询
     */
    @ApiOperation(value = "搜索记录查询")
    @GetMapping("/queryListByPage")
    public PageDataVO<SearchHistoryDTO> searchHistoryReturn(SearchHistoryParam param) {
        return searchHistoryService.searchHistory(param);
    }

    /**
     * 搜索历史-删除搜索记录
     */
    @ApiOperation(value = "删除搜索记录")
    @PostMapping("/delete")
    public void deleteHistory(@RequestParam String id) {
        searchHistoryService.deleteHistory(id);
    }
}



