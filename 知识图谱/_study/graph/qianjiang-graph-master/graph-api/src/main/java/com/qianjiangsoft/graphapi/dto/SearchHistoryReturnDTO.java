package com.qianjiangsoft.graphapi.dto;

import lombok.Data;

import java.util.List;

/**
 * @author ulmarr8861
 * @date 2022/9/19
 */
@Data
public class SearchHistoryReturnDTO {
    private List<SearchHistoryDTO> data;
    private Integer total;
}
