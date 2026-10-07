package com.qianjiangsoft.graphapi.param;

import lombok.Data;

/**
 * @author marshallwu
 * @date 2022/4/24 15:49
 */
@Data
public class TimeLineEventParam {

    private String type;

    private String oppositeName;

    private String oppositeAccount;

    private String spaceId;

    private String time;
}
