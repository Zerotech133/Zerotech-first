package com.qianjiangsoft.graphapi.dto;

import com.qianjiangsoft.graphcore.dto.VertexStatisticsInfoDTO;
import lombok.Data;

import java.util.Map;

/**
 * @author ulmarr8861
 * @date 2022/4/19
 */
@Data
public class GraphVertexReturnDTO {
    private String id;

    private String name;

    private String tagName;

    Map<String, Object> propertyList;

    VertexStatisticsInfoDTO vertexStatisticsInfo;
}
