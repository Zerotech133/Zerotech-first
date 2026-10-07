package com.qianjiangsoft.graphcore.dto;

import com.qianjiangsoft.graphcore.domain.entity.GraphSpace;
import lombok.Data;

import java.util.List;

/**
 * @author ulmarr8861
 * @date 2022/3/29
 */
@Data
public class SelectSpacePageReturnDTO {
    private List<GraphSpace> graphSpaceList;
    private Integer total;
}
