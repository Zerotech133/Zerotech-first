package com.qianjiangsoft.graphcore.service;

import com.qianjiangsoft.graphcore.model.Card;
import com.qianjiangsoft.graphcore.param.card.CreateCardParam;

import java.util.List;

/**
 * @author marshallwu
 * @date 2022/11/29 14:26
 */
public interface SpaceCardService {

    Card createCard(CreateCardParam param);

    List<Card> selectCardListBySpaceId(String spaceId);

    void deleteCardBySpaceId(String spaceId);

}
