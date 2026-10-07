package com.qianjiangsoft.graphapi.service;

import com.qianjiangsoft.graphapi.dto.SearchHistoryDTO;
import com.qianjiangsoft.graphapi.param.history.SearchHistoryParam;
import com.qianjiangsoft.graphcore.model.Space;
import com.qianjiangsoft.graphcore.page.PageDataVO;

public interface SearchHistoryService {

    PageDataVO<SearchHistoryDTO> searchHistory(SearchHistoryParam param);

    void addSearchHistory(String param, String type, Space space);

    void deleteHistory(String id);
}
